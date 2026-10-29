"""Конвейер: источник кадров → анализаторы → шина событий."""

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np

from .analyzers import Analyzer
from .camera import FrameSource
from .events import CameraOffline, CameraOnline, Event

log = logging.getLogger(__name__)

# Упавший анализатор пишет трассировку в лог не чаще раза в минуту
_ERROR_LOG_INTERVAL_S = 60


class Publisher(Protocol):
    def publish(self, event: Event) -> None: ...


class FrameSink(Protocol):
    """Куда отдавать кадры с разметкой: MJPEG-превью, веб-интерфейс."""

    @property
    def has_clients(self) -> bool: ...

    def update(self, image: np.ndarray) -> None: ...


@dataclass
class PipelineStats:
    fps: float = 0.0  # кадров в секунду от камеры
    width: int = 0
    height: int = 0
    last_frame_ts: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)  # среднее время анализатора на кадр
    errors: dict[str, tuple[float, str]] = field(default_factory=dict)  # анализатор → (когда, текст)


class Pipeline:
    def __init__(self, source: FrameSource, analyzers: Sequence[Analyzer], bus: Publisher, camera_name: str,
                 offline_alert_s: float = 0, preview: FrameSink | None = None, preview_fps: float = 10) -> None:
        self._source = source
        self._analyzers = list(analyzers)
        self._bus = bus
        self._camera = camera_name
        self._offline_alert_s = offline_alert_s
        self._preview = preview
        self._preview_interval = 1 / preview_fps
        self._offline_since: float | None = None  # момент пропажи связи, если о ней уже сообщили
        self._error_logged: dict[str, float] = {}
        self._pending: list[Analyzer] | None = None
        self.stats = PipelineStats()

    @property
    def analyzers(self) -> list[Analyzer]:
        return self._analyzers

    def set_analyzers(self, analyzers: Sequence[Analyzer]) -> None:
        """Заменить анализаторы на ходу, не переподключаясь к камере (новые настройки).

        Замена происходит в потоке конвейера между кадрами; анализатор с методом inherit()
        перенимает состояние прежнего с тем же именем (например, «семена закончились»).
        """
        self._pending = list(analyzers)

    def _apply_pending(self) -> None:
        if (pending := self._pending) is None:
            return
        self._pending = None
        previous = {a.name: a for a in self._analyzers}
        for analyzer in pending:
            old = previous.get(analyzer.name)
            if old is not None and type(old) is type(analyzer) and hasattr(analyzer, "inherit"):
                analyzer.inherit(old)
        self._analyzers = pending
        names = {a.name for a in pending}
        for stale in set(self.stats.timings_ms) - names:  # отключённые режимы не показываем
            self.stats.timings_ms.pop(stale, None)
            self.stats.errors.pop(stale, None)

    def run(self, stop: threading.Event) -> None:
        next_run: dict[str, float] = {}
        next_preview = 0.0
        last_index = -1
        fps_mark: tuple[float, int] | None = None
        self._source.start()
        try:
            while not stop.is_set():
                self._apply_pending()
                self._check_camera()
                frame = self._source.wait_frame(last_index, timeout=1.0)
                if frame is None:
                    continue
                last_index = frame.index
                now = time.monotonic()
                stats = self.stats
                stats.height, stats.width = frame.image.shape[:2]
                stats.last_frame_ts = frame.ts
                if fps_mark is None or frame.index < fps_mark[1]:
                    fps_mark = (frame.ts, frame.index)
                elif frame.ts - fps_mark[0] >= 5:
                    stats.fps = (frame.index - fps_mark[1]) / (frame.ts - fps_mark[0])
                    fps_mark = (frame.ts, frame.index)

                analyzers = self._analyzers
                for analyzer in analyzers:
                    if now < next_run.get(analyzer.name, 0.0):
                        continue
                    next_run[analyzer.name] = now + analyzer.interval_s
                    started = time.perf_counter()
                    try:
                        for event in analyzer.process(frame):
                            self._bus.publish(event)
                        stats.errors.pop(analyzer.name, None)
                    except Exception as e:
                        self._analyzer_failed(analyzer.name, frame.index, e)
                    elapsed = (time.perf_counter() - started) * 1000
                    previous = stats.timings_ms.get(analyzer.name)
                    stats.timings_ms[analyzer.name] = elapsed if previous is None else 0.9 * previous + 0.1 * elapsed

                if self._preview is not None and self._preview.has_clients and now >= next_preview:
                    next_preview = now + self._preview_interval
                    image = frame.image.copy()
                    for analyzer in analyzers:
                        analyzer.annotate(image)
                    self._preview.update(image)
        finally:
            self._source.stop()

    def _analyzer_failed(self, name: str, index: int, error: Exception) -> None:
        now = time.time()
        self.stats.errors[name] = (now, f"{type(error).__name__}: {error}")
        if now - self._error_logged.get(name, 0.0) >= _ERROR_LOG_INTERVAL_S:
            self._error_logged[name] = now
            log.exception("Анализатор %s упал на кадре %d", name, index)

    def _check_camera(self) -> None:
        if not self._offline_alert_s:
            return
        connected, since = self._source.connected, self._source.status_since
        if not connected and self._offline_since is None and time.time() - since >= self._offline_alert_s:
            self._offline_since = since
            self._bus.publish(CameraOffline(camera=self._camera, since=since))
        elif connected and self._offline_since is not None:
            self._bus.publish(CameraOnline(camera=self._camera, downtime_s=since - self._offline_since))
            self._offline_since = None
