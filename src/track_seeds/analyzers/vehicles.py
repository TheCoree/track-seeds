"""Пресет «Номера машин»: машина подъехала и остановилась → госномер, цвет, тип."""

from collections.abc import Callable
from typing import Protocol

import cv2
import numpy as np

from ..camera import Frame
from ..config import PlatesConfig
from ..detection import Detection, Detector, merge_detections
from ..events import Event, VehicleArrived, VehicleLeft
from ..plates import PlateRead, VehicleTrack, VehicleTracker, estimate_color, vote


class PlateReaderLike(Protocol):
    def read(self, image: np.ndarray, region: tuple[int, int, int, int] | None = None) -> PlateRead | None: ...


# (камера, номер) → (vehicle_id, время заезда) незакрытого заезда в журнале
type OpenVisitLookup = Callable[[str, str], tuple[str, float] | None]


def _expand(box: tuple[int, int, int, int], shape: tuple[int, ...], margin: float = 0.05) -> tuple[int, int, int, int]:
    """Расширяет рамку машины: номер на бампере бывает на самом краю рамки."""
    x, y, w, h = box
    dx, dy = int(w * margin), int(h * margin)
    x0, y0 = max(x - dx, 0), max(y - dy, 0)
    x1, y1 = min(x + w + dx, shape[1]), min(y + h + dy, shape[0])
    return x0, y0, x1 - x0, y1 - y0


def _in_roi(box: tuple[int, int, int, int], roi: tuple[float, float, float, float] | None,
            shape: tuple[int, ...]) -> bool:
    if roi is None:
        return True
    cx, cy = (box[0] + box[2] / 2) / shape[1], (box[1] + box[3] / 2) / shape[0]
    return roi[0] <= cx <= roi[0] + roi[2] and roi[1] <= cy <= roi[1] + roi[3]


def find_vehicles(detections: list[Detection], settings: PlatesConfig, shape: tuple[int, ...]) -> list[Detection]:
    """Машины из детекций в зоне PLATES_ROI; одна машина с рамками car + truck остаётся одной."""
    vehicles = [d for d in detections
                if d.label in settings.vehicle_labels and d.area_ratio(shape) >= settings.min_vehicle_area
                and _in_roi(d.box, settings.roi, shape)]
    return merge_detections(vehicles, nms=0.5, containment=0.8, class_agnostic=True)


def _jpeg(image: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


class VehicleAnalyzer:
    """Когда машина останавливается, несколько кадров подряд читает номер и голосует.

    Событие «подъехала» уходит, как только номер прочитан confirm_reads раз одинаково,
    либо по таймауту с лучшим вариантом, либо сразу, если машина постояла и поехала
    дальше. «Уехала» — когда машину не видно leave_after_s секунд.

    Машина без прочитанного номера по умолчанию молча пропускается (notify_unreadable).
    Если номер уже числится в журнале незакрытым заездом (процесс перезапускали, а
    машина так и стоит), заезд продолжается без повторного «подъехала».
    """

    def __init__(self, detector: Detector, reader: PlateReaderLike, settings: PlatesConfig, camera_name: str,
                 snapshot_quality: int = 85, open_visit: OpenVisitLookup | None = None) -> None:
        self.name = "plates"
        self.interval_s = settings.interval_s
        self._detector = detector
        self._reader = reader
        self._settings = settings
        self._camera = camera_name
        self._quality = snapshot_quality
        self._open_visit = open_visit
        self._tracker = VehicleTracker(settings.stop_after_s, settings.stop_tolerance, settings.leave_after_s)

    def process(self, frame: Frame) -> list[Event]:
        image = frame.image
        vehicles = find_vehicles(self._detector.detect(image), self._settings, image.shape)
        _, gone = self._tracker.update(vehicles, frame.ts)

        events: list[Event] = []
        for track in self._tracker.tracks:
            if track.reported:
                continue
            if track.stopped_at is not None and track.last_seen == frame.ts:
                if track.arrived_at is None:
                    track.arrived_at = track.stopped_at
                    track.color = estimate_color(image, track.box)
                if read := self._reader.read(image, _expand(track.box, image.shape)):
                    if all(read.confidence > r.confidence for r in track.reads):
                        track.best_frame = image
                    track.reads.append(read)
                _, count = vote(track.reads)
                if count >= self._settings.confirm_reads or frame.ts - track.stopped_at >= self._settings.read_timeout_s:
                    events += self._finish(track, image)
            elif track.stopped_at is None and track.reads:
                # постояла (например, у шлагбаума) и поехала дальше — сообщаем то, что успели прочитать
                events += self._finish(track, image)

        for track in gone:
            if not track.reported and track.reads:
                # постояла, номер начали читать, и машина быстро уехала из кадра
                events += self._finish(track, image)
            if track.reported and not track.silent and track.arrived_at is not None:
                events.append(VehicleLeft(camera=self._camera, ts=track.last_seen, vehicle_id=track.id,
                                          plate=track.plate.display if track.plate else None, color=track.color,
                                          kind=track.kind, duration_s=track.last_seen - track.arrived_at))
        return events

    def _finish(self, track: VehicleTrack, image: np.ndarray) -> list[Event]:
        """Чтение номера закончено: сообщить о заезде, продолжить старый или промолчать."""
        assert track.arrived_at is not None  # читаем номер только после остановки
        best, count = vote(track.reads)
        track.reported = True
        track.plate = best
        if best is None and not self._settings.notify_unreadable:
            track.silent = True
            return []
        if best is not None and self._open_visit and (visit := self._open_visit(self._camera, best.display)):
            track.id, track.arrived_at = visit
            return []
        snapshot = (track.best_frame if track.best_frame is not None else image).copy()
        self._draw(snapshot, track)
        return [VehicleArrived(
            camera=self._camera,
            ts=track.arrived_at,
            vehicle_id=track.id,
            plate=best.display if best else None,
            plate_verified=best is not None and count >= self._settings.confirm_reads,
            plate_confidence=best.confidence if best else 0.0,
            color=track.color or estimate_color(image, track.box),
            kind=track.kind,
            snapshot=_jpeg(snapshot, self._quality),
            plate_image=_jpeg(best.image, 95) if best else None,
        )]

    def annotate(self, image: np.ndarray) -> None:
        if roi := self._settings.roi:
            h, w = image.shape[:2]
            cv2.rectangle(image, (int(roi[0] * w), int(roi[1] * h)),
                          (int((roi[0] + roi[2]) * w) - 1, int((roi[1] + roi[3]) * h) - 1), (255, 128, 0), 2)
        for track in self._tracker.tracks:
            self._draw(image, track)

    def status(self) -> dict:
        tracks = list(self._tracker.tracks)
        return {"tracks": len(tracks), "stopped": sum(t.stopped_at is not None for t in tracks)}

    @staticmethod
    def _draw(image: np.ndarray, track: VehicleTrack) -> None:
        if track.reported:
            color, text = ((0, 200, 0), track.plate.text) if track.plate else ((120, 120, 120), "no plate")
        elif track.stopped_at is not None:
            color, text = (0, 200, 255), f"reading {len(track.reads)}"
        else:
            color, text = (200, 200, 200), "moving"
        x, y, w, h = track.box
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
        cv2.putText(image, f"{track.kind}: {text}", (x, max(y - 8, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2,
                    cv2.LINE_AA)
        plate = track.plate or (track.reads[-1] if track.reads else None)
        if plate is not None:
            px, py, pw, ph = plate.box
            cv2.rectangle(image, (px, py), (px + pw, py + ph), (0, 255, 0), 2)
