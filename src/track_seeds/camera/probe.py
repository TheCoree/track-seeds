"""Проверка подключения к камере: время подключения, разрешение, кодек, реальный fps, снимок."""

import time
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import CameraConfig
from .opencv_source import is_file_source, open_capture


@dataclass
class ProbeResult:
    ok: bool
    error: str = ""
    connect_s: float = 0.0
    backend: str = ""
    width: int = 0
    height: int = 0
    declared_fps: float = 0.0
    codec: str = ""
    frames: int = 0
    measured_fps: float = 0.0
    read_failures: int = 0
    image: np.ndarray | None = None  # последний кадр
    first_image: np.ndarray | None = None  # первый — вместе с последним показывает, движется ли что-то


_HINT = ("Проверьте IP и порт (обычно 554 для RTSP), логин/пароль, путь потока, доступ из этой сети "
         "и что RTSP включён в настройках камеры.")


def probe_camera(settings: CameraConfig, seconds: float = 5) -> ProbeResult:
    started = time.perf_counter()
    cap = open_capture(settings)
    try:
        if not cap.isOpened():
            return ProbeResult(False, f"Не удалось открыть поток за {time.perf_counter() - started:.1f} с. {_HINT}",
                               connect_s=time.perf_counter() - started)
        result = ProbeResult(True, connect_s=time.perf_counter() - started, backend=cap.getBackendName())
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        result.codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip("\0") or "?"
        result.width, result.height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        result.declared_fps = cap.get(cv2.CAP_PROP_FPS)

        started = time.perf_counter()
        while (elapsed := time.perf_counter() - started) < seconds:
            ok, image = cap.read()
            if ok:
                result.frames += 1
                result.image = image
                if result.first_image is None:
                    result.first_image = image
            elif is_file_source(settings.url):
                break  # конец видеофайла
            else:
                result.read_failures += 1
                if result.read_failures >= 5:
                    break
        result.measured_fps = result.frames / elapsed if elapsed > 0 else 0.0
    finally:
        cap.release()

    if result.image is None:
        result.ok, result.error = False, "Поток открылся, но кадры не читаются"
    elif result.read_failures >= 5:
        result.ok, result.error = False, f"Кадры читаются с ошибками ({result.read_failures} подряд)"
    return result
