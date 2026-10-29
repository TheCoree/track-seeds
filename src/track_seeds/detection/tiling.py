"""Детекция по частям кадра для мелких и далёких объектов."""

import math

import numpy as np

from .base import Detection, Detector, iou, merge_detections, overlap_of_smaller


def tile_grid(width: int, height: int) -> list[tuple[int, int, int]]:
    """Квадратные плитки (x, y, сторона) вдоль длинной стороны кадра с перекрытием ≥10%.

    Для кадра, близкого к квадрату, плитки не нужны: возвращает пустой список.
    """
    side, length = min(width, height), max(width, height)
    if length < side * 1.15:
        return []
    count = math.ceil(length / side * 1.1)
    span = length - side
    offsets = [round(i * span / (count - 1)) for i in range(count)]
    return [(o, 0, side) if width > height else (0, o, side) for o in offsets]


class TiledDetector:
    """Прогоняет детектор по целому кадру и по плиткам, затем сливает результаты.

    Сеть уменьшает кадр до своего входа (416×416), и на широком кадре далёкие или
    сидящие люди становятся слишком мелкими. В плитке 16:9-кадра они крупнее в ~1.8 раза.
    Целый кадр нужен для крупных объектов, которые попали на стык плиток.

    Плитки только добавляют то, что целый кадр пропустил: рамка из плитки отбрасывается,
    если совпадает с рамкой целого кадра или лежит внутри неё, а также если упирается во
    внутренний край плитки. Иначе обрезанная половина машины из плитки с той же или
    большей уверенностью вытесняет при слиянии настоящую рамку.
    """

    _EDGE = 0.02  # доля стороны плитки

    def __init__(self, inner: Detector, nms: float, containment: float) -> None:
        self._inner = inner
        self._nms = nms
        self._containment = containment

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        full = list(self._inner.detect(image))
        extra: list[Detection] = []
        for x0, y0, side in tile_grid(width, height):
            edge = max(3, int(side * self._EDGE))
            for d in self._inner.detect(image[y0:y0 + side, x0:x0 + side]):
                x, y, w, h = d.box
                cut = ((x <= edge and x0 > 0) or (x + w >= side - edge and x0 + side < width)
                       or (y <= edge and y0 > 0) or (y + h >= side - edge and y0 + side < height))
                shifted = Detection(d.label, d.confidence, (x + x0, y + y0, w, h))
                if not cut and not any(self._duplicates(shifted, f) for f in full):
                    extra.append(shifted)
        return merge_detections(full + extra, self._nms, self._containment)

    def _duplicates(self, tile_det: Detection, full_det: Detection) -> bool:
        """Рамка из плитки — урезанная версия того же объекта с целого кадра.

        Рамка из плитки того же или большего размера полезна (в плитке объект крупнее,
        рамка точнее и увереннее) — с дублем разберётся merge_detections.
        """
        if tile_det.label != full_det.label or tile_det.box[2] * tile_det.box[3] >= full_det.box[2] * full_det.box[3]:
            return False
        return (iou(tile_det.box, full_det.box) > self._nms
                or overlap_of_smaller(tile_det.box, full_det.box) > self._containment)
