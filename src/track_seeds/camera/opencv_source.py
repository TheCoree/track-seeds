"""Источник кадров на OpenCV VideoCapture: IP-камера (RTSP/HTTP), вебкамера или видеофайл."""

import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import cv2

from ..config import CameraConfig
from .base import Frame

log = logging.getLogger(__name__)


def mask_url(url: str) -> str:
    """Скрывает пароль в URL для логов."""
    parts = urlsplit(url)
    if not parts.password:
        return url
    netloc = f"{parts.username}:***@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


def is_file_source(url: str) -> bool:
    return Path(url).is_file()


def stream_url(settings: CameraConfig) -> str:
    """URL потока с логином/паролем из CAMERA_USERNAME/CAMERA_PASSWORD."""
    parts = urlsplit(settings.url)
    if not settings.username or parts.scheme not in ("rtsp", "rtsps", "http", "https") or parts.username:
        return settings.url
    userinfo = quote(settings.username, safe="")
    if settings.password:
        userinfo += ":" + quote(settings.password.get_secret_value(), safe="")
    return urlunsplit(parts._replace(netloc=f"{userinfo}@{parts.netloc}"))


def open_capture(settings: CameraConfig) -> cv2.VideoCapture:
    if settings.url.isdigit():
        return cv2.VideoCapture(int(settings.url))
    url = stream_url(settings)
    # Опции FFmpeg читаются при каждом открытии потока; TCP надёжнее UDP через NAT/Wi-Fi.
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", f"rtsp_transport;{settings.rtsp_transport}")
    params = [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(settings.open_timeout_s * 1000),
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(settings.read_timeout_s * 1000),
    ]
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)


class OpenCVSource:
    """Читает кадры в фоновом потоке и хранит только последний.

    Детекция идёт со своей частотой и всегда получает свежий кадр, а не
    накопившийся в буфере декодера. При обрыве — переподключение с backoff.
    Видеофайлы проигрываются по кругу в реальном темпе (для разработки).
    """

    def __init__(self, settings: CameraConfig) -> None:
        self._settings = settings
        self._is_file = is_file_source(settings.url)
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._index = -1
        self._connected = False
        self._status_since = time.time()
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def status_since(self) -> float:
        return self._status_since

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(self._settings.read_timeout_s + 2)
            self._thread = None

    def wait_frame(self, after_index: int, timeout: float) -> Frame | None:
        with self._cond:
            self._cond.wait_for(
                lambda: self._stop.is_set() or (self._frame is not None and self._frame.index > after_index),
                timeout,
            )
            frame = self._frame
        return frame if frame is not None and frame.index > after_index else None

    def _set_connected(self, value: bool) -> None:
        if value != self._connected:
            self._connected = value
            self._status_since = time.time()

    def _run(self) -> None:
        name = mask_url(self._settings.url)
        delay = self._settings.reconnect_delay_s
        while not self._stop.is_set():
            log.info("Подключение к камере %s", name)
            cap = open_capture(self._settings)
            if not cap.isOpened():
                cap.release()
                self._set_connected(False)
                log.warning("Камера %s недоступна, повтор через %.0f с", name, delay)
                self._stop.wait(delay)
                delay = min(delay * 2, self._settings.reconnect_max_delay_s)
                continue

            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            log.info("Камера подключена: %dx%d, %.1f fps", w, h, cap.get(cv2.CAP_PROP_FPS))
            try:
                got_frames = self._read_loop(cap)
            finally:
                cap.release()
                self._set_connected(False)
            if self._stop.is_set():
                break
            if got_frames:
                delay = self._settings.reconnect_delay_s
            log.warning("Поток с камеры %s прервался, переподключение через %.0f с", name, delay)
            self._stop.wait(delay)
            if not got_frames:
                delay = min(delay * 2, self._settings.reconnect_max_delay_s)

    def _read_loop(self, cap: cv2.VideoCapture) -> bool:
        """Читает кадры до обрыва потока; возвращает True, если был получен хоть один кадр."""
        fps = cap.get(cv2.CAP_PROP_FPS) if self._is_file else 0
        frame_interval = 1 / fps if fps > 0 else 0
        got_frames = rewound = False
        while not self._stop.is_set():
            ok, image = cap.read()
            if not ok:
                if self._is_file and not rewound:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    rewound = True
                    continue
                return got_frames
            got_frames = True
            rewound = False
            self._set_connected(True)
            with self._cond:
                self._index += 1
                self._frame = Frame(image=image, index=self._index, ts=time.time())
                self._cond.notify_all()
            if frame_interval:
                self._stop.wait(frame_interval)
