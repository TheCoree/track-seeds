"""Зона привода (цепь, звёздочка, вал): движется — дробилка работает, неподвижна — стоит.

Сравниваются два кадра зоны с интервалом проверки (около секунды): считается доля пикселей,
которые заметно изменились. Общая смена яркости (экспозиция камеры, свет) выравнивается
до сравнения, шум матрицы и сжатия порогом не проходит.

На записи с дробилок: крутящийся привод — 19–31% зоны за секунду, неподвижные участки — до 1%.
"""

from collections.abc import Sequence

import cv2
import numpy as np

from .zone import Point, extract_patch

# Пиксель изменился, если разница больше _MIN_DIFF уровней яркости и больше _REL_DIFF от средней яркости зоны
_MIN_DIFF, _REL_DIFF = 10.0, 0.08


def motion_patch(image: np.ndarray, points: Sequence[Point]) -> np.ndarray:
    gray = cv2.cvtColor(extract_patch(image, points), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return cv2.GaussianBlur(gray, (3, 3), 0.8)


def changed_share(current: np.ndarray, previous: np.ndarray) -> float:
    """Доля пикселей зоны, изменившихся между двумя кадрами: 0..1."""
    mean = float(current.mean())
    previous = previous * (mean / max(float(previous.mean()), 1.0))
    return float(np.mean(np.abs(current - previous) > max(_MIN_DIFF, _REL_DIFF * mean)))
