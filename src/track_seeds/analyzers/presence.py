"""Присутствие людей в кадре: «вошёл», «ушёл», «изменилось число людей» с защитой от дребезга."""

import statistics
from collections import deque
from enum import Enum

import cv2
import numpy as np

from ..camera import Frame
from ..config import PresenceConfig
from ..detection import Detection, Detector, draw_detections
from ..events import Event, PeopleCountChanged, PersonEntered, PersonLeft


class Transition(Enum):
    ENTERED = "entered"
    LEFT = "left"
    COUNT_CHANGED = "count_changed"


class PresenceTracker:
    """Конечный автомат присутствия.

    Появление подтверждается enter_hits детекциями подряд (отсекает единичные ложные
    срабатывания), уход — отсутствием детекций leave_after_s секунд (человек может
    на мгновение пропасть из-за перекрытия или неудачного ракурса).

    Число людей сглаживается медианой за count_window_s: детектор то теряет, то находит
    частично закрытого человека. Об изменении сообщается, только если новое значение
    продержалось count_change_after_s секунд. Переход в 0 — это не изменение числа, а уход.
    """

    def __init__(self, enter_hits: int, leave_after_s: float,
                 count_window_s: float = 5, count_change_after_s: float = 10) -> None:
        self._enter_hits = enter_hits
        self._leave_after_s = leave_after_s
        self._count_window_s = count_window_s
        self._count_change_after_s = count_change_after_s
        self._samples: deque[tuple[float, int]] = deque()
        self._hits = 0
        self._hit_max = 0
        self._first_hit_ts = 0.0
        self._pending: tuple[int, float] | None = None  # (новое число, с какого момента держится)
        self.present = False
        self.count = 0
        self.previous_count = 0
        self.entered_at = 0.0
        self.last_seen = 0.0

    def update(self, count: int, ts: float) -> Transition | None:
        self._samples.append((ts, count))
        while self._samples[0][0] < ts - self._count_window_s:
            self._samples.popleft()
        return self._update_present(count, ts) if self.present else self._update_absent(count, ts)

    def _update_absent(self, count: int, ts: float) -> Transition | None:
        if not count:
            self._hits = 0
            return None
        if self._hits == 0:
            self._first_hit_ts = ts
            self._hit_max = 0
        self._hits += 1
        self._hit_max = max(self._hit_max, count)
        if self._hits < self._enter_hits:
            return None
        self.present = True
        self.entered_at = self._first_hit_ts
        self.last_seen = ts
        self.previous_count, self.count = 0, self._hit_max
        self._pending = None
        return Transition.ENTERED

    def _update_present(self, count: int, ts: float) -> Transition | None:
        if count:
            self.last_seen = ts
        elif ts - self.last_seen >= self._leave_after_s:
            self.present = False
            self._hits = 0
            self.previous_count, self.count = self.count, 0
            return Transition.LEFT

        # median_high: пропуск детекции случается чаще ложного срабатывания
        smoothed = statistics.median_high(c for _, c in self._samples)
        if smoothed in (0, self.count):
            self._pending = None
            return None
        if self._pending is None or self._pending[0] != smoothed:
            self._pending = (smoothed, ts)
            return None
        if ts - self._pending[1] < self._count_change_after_s:
            return None
        self.previous_count, self.count = self.count, smoothed
        self._pending = None
        return Transition.COUNT_CHANGED


class PresenceAnalyzer:
    def __init__(self, detector: Detector, settings: PresenceConfig, camera_name: str,
                 snapshot_quality: int = 85) -> None:
        self.name = "presence"
        self.interval_s = settings.interval_s
        self._detector = detector
        self._settings = settings
        self._camera = camera_name
        self._quality = snapshot_quality
        self._tracker = PresenceTracker(settings.enter_hits, settings.leave_after_s,
                                        settings.count_window_s, settings.count_change_after_s)
        self._last: list[Detection] = []

    def process(self, frame: Frame) -> list[Event]:
        self._last = [
            d for d in self._detector.detect(frame.image)
            if d.label == self._settings.label and d.area_ratio(frame.image.shape) >= self._settings.min_box_area
        ]
        tracker = self._tracker
        match tracker.update(len(self._last), frame.ts):
            case Transition.ENTERED:
                return [PersonEntered(camera=self._camera, ts=frame.ts, count=tracker.count,
                                      snapshot=self._snapshot(frame.image))]
            case Transition.LEFT:
                return [PersonLeft(camera=self._camera, ts=frame.ts,
                                   duration_s=tracker.last_seen - tracker.entered_at,
                                   snapshot=self._snapshot(frame.image))]
            case Transition.COUNT_CHANGED if self._settings.count_changes:
                return [PeopleCountChanged(camera=self._camera, ts=frame.ts, count=tracker.count,
                                           previous=tracker.previous_count, snapshot=self._snapshot(frame.image))]
        return []

    def annotate(self, image: np.ndarray) -> None:
        draw_detections(image, self._last)
        tracker = self._tracker
        text = f"people: {tracker.count} (frame: {len(self._last)})" if tracker.present else "people: none"
        color = (0, 0, 255) if tracker.present else (200, 200, 200)
        cv2.putText(image, text, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2, cv2.LINE_AA)

    def status(self) -> dict:
        return {"present": self._tracker.present, "count": self._tracker.count, "in_frame": len(self._last)}

    def _snapshot(self, image: np.ndarray) -> bytes:
        annotated = image.copy()
        draw_detections(annotated, self._last)
        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        return buf.tobytes() if ok else b""
