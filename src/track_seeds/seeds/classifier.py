"""Оценка «есть ли семена в зоне» по эталонам, снятым при настройке."""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from .features import Features, distance

# Когда эталоны только одного вида: радиус «похоже на эталон», если эталон один,
# и множитель к разбросу между эталонами, если их несколько.
_ONE_CLASS_RADIUS = 0.15
_ONE_CLASS_SPREAD = 3.0
_MIN_SEPARATION = 0.02
# «Непохоже на эталоны» меряется относительно разделения «есть»/«пусто», но не больше этого
# расстояния: если классы очень разные, шум или чужая текстура иначе сойдут за «ближе к семенам».
# На фото семян и металла: свой класс при том же свете — до 0.15 (p99), при сильной смене света — до 0.32.
_NOVELTY_SCALE_MAX = 0.35


@dataclass(frozen=True, slots=True)
class Score:
    value: float  # 0..1: 1 — похоже на «семена есть», 0 — на «пусто»
    d_full: float | None  # расстояние до ближайшего эталона «есть семена»
    d_empty: float | None  # … «пусто»
    novelty: float | None  # во сколько раз кадр дальше от эталонов, чем «есть» и «пусто» друг от друга (≤0.35)


def confidence(score: float, threshold: float = 0.5) -> float:
    """Уверенность «семена есть» 0..1 для показа человеку. Граница та же, что у оценки (threshold → 0.5),
    но без «серой середины»: оценка — отношение расстояний до эталонов, и кадр, который явно ближе
    к семенам, чем граница, даёт 0.85–0.95, хотя сомнений нет. Здесь отношение шансов относительно
    границы возводится в 4-ю степень: 0.75 → 99 %, 0.9 → 100 %, 0.6 → 84 %, 0.5 → 50 %, 0.2 → 0 %."""
    if score <= 0 or score >= 1:
        return 1.0 if score >= 1 else 0.0
    odds = (score / (1 - score)) / (threshold / (1 - threshold))
    return odds ** 4 / (1 + odds ** 4)


def _nearest(f: Features, samples: Sequence[Features]) -> float | None:
    return min((distance(f, s) for s in samples), default=None)


def _radius(samples: Sequence[Features]) -> float:
    """Радиус «похоже» для одного вида эталонов. Эталоны, снятые при разном свете, его увеличивают;
    снятые подряд (почти одинаковые) — не уменьшают: иначе порог стал бы сверхстрогим."""
    if len(samples) < 2:
        return _ONE_CLASS_RADIUS
    nearest = [min(distance(a, b) for j, b in enumerate(samples) if j != i) for i, a in enumerate(samples)]
    return max(_ONE_CLASS_SPREAD * statistics.median(nearest), _ONE_CLASS_RADIUS)


class ZoneModel:
    """Ближайший сосед среди эталонов «есть семена» / «пусто».

    Эталонов может быть несколько каждого вида (днём, ночью, при разном свете) —
    сравнение идёт с ближайшим. Если кадр далеко от всех эталонов (novelty > 1: дальше,
    чем «есть» от «пусто»), картинка изменилась непонятно как: крышка, пыль, сдвиг камеры.
    """

    def __init__(self, full: Sequence[Features], empty: Sequence[Features], tolerance: float = 1.0) -> None:
        self.full = list(full)
        self.empty = list(empty)
        self.separation: float | None = None
        if self.full and self.empty:
            cross = min(distance(f, e) for f in self.full for e in self.empty)
            self.separation = max(cross, _MIN_SEPARATION)
        # допуск «похоже на эталон», когда эталоны только одного вида; tolerance — ручная поправка
        self.radius = _radius(self.full or self.empty) * tolerance

    @property
    def ready(self) -> bool:
        return bool(self.full or self.empty)

    @property
    def one_class(self) -> bool:
        return self.ready and not (self.full and self.empty)

    def score(self, f: Features) -> Score:
        d_full, d_empty = _nearest(f, self.full), _nearest(f, self.empty)
        if d_full is not None and d_empty is not None:
            assert self.separation is not None
            value = d_empty / (d_full + d_empty) if d_full + d_empty > 0 else 0.5
            return Score(value, d_full, d_empty, min(d_full, d_empty) / min(self.separation, _NOVELTY_SCALE_MAX))
        if d_full is not None:
            # только «есть семена»: всё, что непохоже, считается пустым
            return Score(self.radius / (self.radius + d_full), d_full, None, None)
        if d_empty is not None:
            return Score(d_empty / (self.radius + d_empty), None, d_empty, None)
        raise ValueError("Нет эталонов")
