from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class Detection:
    label: str
    confidence: float
    box: tuple[int, int, int, int]  # x, y, w, h в пикселях исходного кадра

    def area_ratio(self, image_shape: tuple[int, ...]) -> float:
        return self.box[2] * self.box[3] / (image_shape[0] * image_shape[1])


class Detector(Protocol):
    def detect(self, image: np.ndarray) -> list[Detection]: ...


class FrameCachedDetector:
    """Если несколько анализаторов спрашивают про один и тот же кадр — детекция одна."""

    def __init__(self, inner: Detector) -> None:
        self._inner = inner
        self._image: np.ndarray | None = None
        self._result: list[Detection] = []

    def detect(self, image: np.ndarray) -> list[Detection]:
        if image is not self._image:
            self._result = self._inner.detect(image)
            self._image = image
        return self._result


type Box = tuple[int, int, int, int]


def _intersection(a: Box, b: Box) -> int:
    ix = max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy


def iou(a: Box, b: Box) -> float:
    inter = _intersection(a, b)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


def overlap_of_smaller(a: Box, b: Box) -> float:
    """Какая доля меньшей рамки лежит внутри большей."""
    smaller = min(a[2] * a[3], b[2] * b[3])
    return _intersection(a, b) / smaller if smaller else 1.0


def merge_detections(detections: list[Detection], nms: float, containment: float,
                     class_agnostic: bool = False) -> list[Detection]:
    """NMS по классам + подавление вложенных рамок.

    Обычный NMS не убирает маленькую рамку внутри большой (например, «торс» внутри
    «человека»): их IoU мало, и один человек считается за двоих. Такая рамка удаляется,
    если доля её площади внутри более уверенной рамки того же класса больше containment.
    class_agnostic — сливать рамки разных классов (одну машину детектор называет то car, то truck).
    """
    if not detections:
        return []
    class_ids = {label: i for i, label in enumerate(dict.fromkeys(d.label for d in detections))}
    keep = cv2.dnn.NMSBoxesBatched([list(d.box) for d in detections], [d.confidence for d in detections],
                                   [0 if class_agnostic else class_ids[d.label] for d in detections], 0.0, nms)
    candidates = sorted((detections[i] for i in np.asarray(keep).reshape(-1)), key=lambda d: -d.confidence)
    result: list[Detection] = []
    for det in candidates:
        if not any((class_agnostic or k.label == det.label) and overlap_of_smaller(det.box, k.box) > containment
                   for k in result):
            result.append(det)
    return result


def draw_detections(image: np.ndarray, detections: list[Detection], color=(0, 200, 0)) -> None:
    for det in detections:
        x, y, w, h = det.box
        cv2.rectangle(image, (x, y), (x + w, y + h), color, 2)
        cv2.putText(image, f"{det.label} {det.confidence:.2f}", (x, max(y - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
