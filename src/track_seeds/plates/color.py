"""Примерный цвет кузова по рамке машины.

Берётся средняя часть рамки (двери/капот, без колёс, стёкол и фона по краям), каждый
пиксель относится к одному из названных цветов по HSV, побеждает самый частый.
Тёмные пиксели (решётка, тени, стёкла) учитываются, только если их большинство —
тогда машина чёрная. Освещение сильно влияет на результат, поэтому цвет примерный.
"""

import cv2
import numpy as np

_DARK_V = 50
_GREY_S = 45


def estimate_color(image: np.ndarray, box: tuple[int, int, int, int]) -> str:
    x, y, w, h = box
    roi = image[y + int(h * 0.35):y + int(h * 0.8), x + int(w * 0.15):x + int(w * 0.85)]
    if roi.size == 0:
        return "неизвестно"
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    hue, sat, val = hsv[:, 0], hsv[:, 1], hsv[:, 2]

    dark = val < _DARK_V
    if dark.mean() > 0.5:
        return "чёрный"

    names = np.full(len(hsv), "", dtype=object)
    # Оттенок серого решаем по медиане всей серой гаммы: попиксельно тени и блики
    # одной серебристой двери разъезжаются в «белый» и «серый».
    grey = ~dark & (sat < _GREY_S)
    names[grey] = "grey"

    colored = ~dark & ~grey
    bins = [
        ((hue < 8) | (hue >= 165), "красный"),
        ((hue >= 8) & (hue < 22), "оранжевый"),
        ((hue >= 22) & (hue < 35), "жёлтый"),
        ((hue >= 35) & (hue < 85), "зелёный"),
        ((hue >= 85) & (hue < 100), "голубой"),
        ((hue >= 100) & (hue < 130), "синий"),
        ((hue >= 130) & (hue < 165), "фиолетовый"),
    ]
    for mask, name in bins:
        names[colored & mask] = name
    # приглушённые тёплые оттенки — коричневый/бежевый, а не оранжевый/жёлтый; как и серые,
    # считаются одной группой, а оттенок выбирается по медиане яркости
    warm = colored & (hue >= 5) & (hue < 35) & ((sat < 110) | (val < 140))
    names[warm] = "warm"

    labels, counts = np.unique(names[~dark], return_counts=True)
    if not len(labels):
        return "чёрный"
    match str(labels[counts.argmax()]):
        case "grey":
            median_val = np.median(val[grey])
            return "белый" if median_val >= 215 else "серебристый" if median_val >= 140 else "серый"
        case "warm":
            return "бежевый" if np.median(val[warm]) >= 170 else "коричневый"
        case name:
            return name
