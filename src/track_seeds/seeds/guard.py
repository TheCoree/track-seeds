"""Стенки вокруг зоны: «пусто» в зоне — семена кончились или её загородили.

Эталонов «пусто» может не быть, и тогда всё, что непохоже на семена, выглядит пустым желобом:
рука, мешок, лопата, сдвинутая камера. Но загородить зону, не задев края желоба, почти нельзя:
рука тянется из-за стенки, мешок шире желоба. А когда семена кончаются, меняется только то,
что внутри, — стенки остаются как были.

Стенки сравниваются не с эталонами (свет за день меняется, желоб после ремонта ставят чуть иначе),
а с тем, как они выглядели только что: опорный кадр стенок обновляется, пока они почти не меняются
или пока в зоне видны семена (значит, перед ней ничего нет). Резкое изменение стенок — «загородили».

На кадрах дробилок: стенки работающей дробилки за 0,5–15 с меняются на 0–6% пикселей.
"""

from collections.abc import Sequence

import numpy as np

from .motion import changed_share, motion_patch
from .zone import Point

# Стенки не меняются — опорный кадр следует за светом; неподвижны — можно взять за опорный;
# изменились на столько — перед зоной что-то есть
_FOLLOW, _STEADY, _BLOCKED = 0.1, 0.08, 0.25


def _changed(current: list[np.ndarray], previous: list[np.ndarray] | None) -> float | None:
    if previous is None or any(a.shape != b.shape for a, b in zip(current, previous)):
        return None
    return max(changed_share(a, b) for a, b in zip(current, previous))


class ZoneGuard:
    def __init__(self, quads: Sequence[Sequence[Point]]) -> None:
        self.quads = [list(q) for q in quads]
        self.changed: float | None = None  # насколько стенки отличаются от опорного кадра (доля пикселей)
        self._reference: list[np.ndarray] | None = None
        self._previous: list[np.ndarray] | None = None

    def update(self, image: np.ndarray, seeds_seen: bool) -> bool:
        """Каждый кадр. seeds_seen — в зоне видны семена. True — стенки резко изменились: зону загородили."""
        patches = [motion_patch(image, q) for q in self.quads]
        motion = _changed(patches, self._previous)
        self._previous = patches
        steady = motion is not None and motion < _STEADY
        self.changed = _changed(patches, self._reference)
        if self.changed is None:
            # опорного кадра нет (запуск): пока у стенок что-то движется, не решаем
            if steady or seeds_seen:
                self._reference, self.changed = patches, 0.0
            return self._reference is None
        # Семена видны и у стенок ничего не движется — опорный кадр сменился (свет включили, что-то
        # поставили рядом). Пока рабочий ворошит семена, опорным остаётся кадр до него.
        if self.changed < _FOLLOW or seeds_seen and steady:
            self._reference = patches
        return self.changed >= _BLOCKED
