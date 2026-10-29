"""Признаки фрагмента зоны и расстояние между ними.

Семена — мелкая зернистая текстура, пустой бункер — гладкий металл/дерево со своим
цветом и бликами. Признаки — нормированные гистограммы, сравниваются по χ²:

- texture: локальные бинарные шаблоны (LBP, инвариантные к повороту) на двух масштабах —
  форма мелких перепадов, почти не зависит от яркости освещения;
- edges: сила перепадов относительно средней яркости (зерно — много сильных перепадов);
- tone: распределение яркости (тёмные семечки на светлом металле);
- color: оттенок × насыщенность; на чёрно-белом кадре (ночь, ИК) не используется.
"""

from dataclasses import dataclass

import cv2
import numpy as np

_OFFSETS = ((-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1))
_EDGE_BINS = np.array([0, 0.015, 0.03, 0.06, 0.1, 0.16, 0.25, 0.4, np.inf], np.float32)
_HUE_BINS, _SAT_SPLIT, _MIN_SAT, _MIN_VALUE = 12, 120, 40, 40

# Вес группы признаков в итоговом расстоянии
WEIGHTS = {"texture": 1.5, "edges": 1.0, "tone": 0.5, "color": 1.0}


@dataclass(frozen=True, slots=True)
class Features:
    texture: np.ndarray
    edges: np.ndarray
    tone: np.ndarray
    color: np.ndarray | None  # None — кадр чёрно-белый
    brightness: float  # средняя яркость 0..255


def _normalized(hist: np.ndarray) -> np.ndarray:
    hist = hist.astype(np.float32).ravel()
    total = hist.sum()
    return hist / total if total else hist


def _lbp(gray: np.ndarray, threshold: float) -> np.ndarray:
    """Гистограмма LBP riu2 (8 соседей): 0–8 — «равномерные» шаблоны по числу единиц, 9 — остальные."""
    h, w = gray.shape
    center = gray[1:-1, 1:-1]
    bits = np.stack([gray[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx] - center >= threshold for dy, dx in _OFFSETS])
    transitions = (bits != np.roll(bits, 1, axis=0)).sum(axis=0)
    codes = np.where(transitions <= 2, bits.sum(axis=0), 9)
    return _normalized(np.bincount(codes.ravel(), minlength=10))


def extract(patch: np.ndarray, grayscale: bool) -> Features:
    """patch — выпрямленный фрагмент зоны (BGR); grayscale — весь кадр чёрно-белый."""
    gray = cv2.GaussianBlur(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0.8)
    brightness = float(gray.mean())
    # Порог LBP растёт с яркостью: шум матрицы и сжатия не должен выглядеть как текстура
    threshold = 2 + 0.02 * brightness
    small = cv2.pyrDown(gray)
    texture = np.concatenate([_lbp(gray, threshold), _lbp(small, threshold) if min(small.shape) >= 8 else
                              np.zeros(10, np.float32)])

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    relative = cv2.magnitude(gx, gy) / 8 / (brightness + 20)
    edges = _normalized(np.histogram(relative, bins=_EDGE_BINS)[0])

    tone = _normalized(np.histogram(gray, bins=16, range=(0, 256))[0])

    color = None
    if not grayscale:
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hue, sat, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        vivid = (sat >= _MIN_SAT) & (value >= _MIN_VALUE)
        index = (hue.astype(np.int32) * _HUE_BINS // 180) * 2 + (sat >= _SAT_SPLIT)
        hist = np.bincount(index[vivid], minlength=_HUE_BINS * 2)
        color = _normalized(np.append(hist, np.count_nonzero(~vivid)))  # последняя корзина — серые пиксели
    return Features(texture=texture, edges=edges, tone=tone, color=color, brightness=brightness)


def _chi2(a: np.ndarray, b: np.ndarray) -> float:
    total = a + b
    mask = total > 0
    return 0.5 * float(np.sum((a[mask] - b[mask]) ** 2 / total[mask]))


def distance(a: Features, b: Features) -> float:
    """0 — одинаковые, 1 — ничего общего. Группы, которых нет у одного из фрагментов, пропускаются."""
    total = weight = 0.0
    for name, w in WEIGHTS.items():
        x, y = getattr(a, name), getattr(b, name)
        if x is None or y is None:
            continue
        total += w * _chi2(x, y)
        weight += w
    return total / weight
