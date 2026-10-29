"""Сопровождение машин между кадрами и определение остановки."""

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..detection import Detection, iou
from .reader import PlateRead

# Перерыв в наблюдении дольше этого сбрасывает отсчёт неподвижности: машину могли
# не видеть, пока она двигалась.
_MAX_GAP_S = 2.0


def _center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


@dataclass(eq=False)
class VehicleTrack:
    id: str
    box: tuple[int, int, int, int]
    first_seen: float
    last_seen: float
    seen_since: float  # начало непрерывного наблюдения
    anchor: tuple[float, float]  # центр в начале текущего периода неподвижности
    anchor_ts: float
    labels: Counter[str] = field(default_factory=Counter)
    stopped_at: float | None = None  # None — едет
    reads: list[PlateRead] = field(default_factory=list)
    arrived_at: float | None = None  # первая остановка
    reported: bool = False  # чтение номера закончено, решение по машине принято
    silent: bool = False  # не сообщать об этой машине (номер не прочитан, PLATES_NOTIFY_UNREADABLE=false)
    plate: PlateRead | None = None  # итоговое прочтение
    color: str = ""
    best_frame: np.ndarray | None = field(default=None, repr=False)  # кадр с самым уверенным прочтением

    @property
    def kind(self) -> str:
        return self.labels.most_common(1)[0][0]


class VehicleTracker:
    """Сопоставляет машины между кадрами по перекрытию рамок (IoU) и отмечает остановку.

    Машина считается остановившейся, если её центр смещается меньше чем на
    stop_tolerance × диагональ рамки в течение stop_after_s непрерывного наблюдения.
    Машина без детекций удаляется только через leave_after_s, поэтому кратковременное
    перекрытие (прошёл человек) не превращает её в новую.
    """

    def __init__(self, stop_after_s: float, stop_tolerance: float, leave_after_s: float,
                 min_iou: float = 0.3) -> None:
        self._stop_after_s = stop_after_s
        self._stop_tolerance = stop_tolerance
        self._leave_after_s = leave_after_s
        self._min_iou = min_iou
        self.tracks: list[VehicleTrack] = []

    def update(self, detections: list[Detection], ts: float) -> tuple[list[VehicleTrack], list[VehicleTrack]]:
        """Возвращает (только что остановившиеся, пропавшие) машины."""
        pairs = sorted(((iou(t.box, d.box), ti, di) for ti, t in enumerate(self.tracks)
                        for di, d in enumerate(detections)), reverse=True)
        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        for score, ti, di in pairs:
            if score < self._min_iou:
                break
            if ti in used_tracks or di in used_detections:
                continue
            used_tracks.add(ti)
            used_detections.add(di)
            self._follow(self.tracks[ti], detections[di], ts)

        for di, det in enumerate(detections):
            if di not in used_detections:
                center = _center(det.box)
                self.tracks.append(VehicleTrack(id=uuid.uuid4().hex[:12], box=det.box, first_seen=ts, last_seen=ts,
                                                seen_since=ts, anchor=center, anchor_ts=ts,
                                                labels=Counter([det.label])))

        stopped = []
        for track in self.tracks:
            still_since = max(track.anchor_ts, track.seen_since)
            if track.stopped_at is None and track.last_seen == ts and ts - still_since >= self._stop_after_s:
                track.stopped_at = ts
                stopped.append(track)

        gone = [t for t in self.tracks if ts - t.last_seen >= self._leave_after_s]
        self.tracks = [t for t in self.tracks if t not in gone]
        return stopped, gone

    def _follow(self, track: VehicleTrack, det: Detection, ts: float) -> None:
        if ts - track.last_seen > _MAX_GAP_S:
            track.seen_since = ts
        track.box = det.box
        track.last_seen = ts
        track.labels[det.label] += 1
        center = _center(det.box)
        if math.dist(center, track.anchor) > self._stop_tolerance * math.hypot(det.box[2], det.box[3]):
            track.anchor, track.anchor_ts = center, ts
            track.stopped_at = None
