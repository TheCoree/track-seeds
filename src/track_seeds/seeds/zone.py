"""Зона кадра, где видно семена: четырёхугольник в долях кадра → выпрямленный фрагмент."""

import math
from collections.abc import Sequence

import cv2
import numpy as np

type Point = tuple[float, float]

# Фрагмент зоны приводится к масштабу кадра шириной 640 px: эталоны и живые кадры сравниваются
# в одном масштабе, даже если поток переключили с основного на субпоток.
_REF_WIDTH = 640
_MIN_SIDE, _MAX_SIDE = 24, 160


def order_quad(points: Sequence[Point]) -> list[Point]:
    """Углы по часовой стрелке начиная с левого верхнего — как бы их ни перетащили в интерфейсе."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    ordered = sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    start = min(range(len(ordered)), key=lambda i: ordered[i][0] + ordered[i][1])
    return [tuple(p) for p in ordered[start:] + ordered[:start]]  # type: ignore[misc]


def quad_pixels(points: Sequence[Point], shape: tuple[int, ...]) -> np.ndarray:
    h, w = shape[:2]
    return np.array([(x * w, y * h) for x, y in order_quad(points)], np.float32)


def zone_box(points: Sequence[Point], shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    """Описанный прямоугольник зоны в пикселях: x, y, w, h."""
    x, y, w, h = cv2.boundingRect(quad_pixels(points, shape))
    return x, y, w, h


def _sides(quad: np.ndarray) -> tuple[float, float]:
    """Средняя ширина и высота четырёхугольника."""
    d = lambda a, b: float(np.linalg.norm(quad[a] - quad[b]))  # noqa: E731
    return (d(0, 1) + d(3, 2)) / 2, (d(0, 3) + d(1, 2)) / 2


def patch_size(points: Sequence[Point], shape: tuple[int, ...]) -> tuple[int, int]:
    quad = quad_pixels(points, shape)
    width, height = _sides(quad)
    scale = _REF_WIDTH / shape[1]
    w, h = width * scale, height * scale
    k = min(1.0, _MAX_SIDE / max(w, h, 1))
    return max(round(w * k), _MIN_SIDE), max(round(h * k), _MIN_SIDE)


def extract_patch(image: np.ndarray, points: Sequence[Point]) -> np.ndarray:
    """Выпрямленный фрагмент зоны фиксированного (для этой зоны) размера."""
    quad = quad_pixels(points, image.shape)
    pw, ph = patch_size(points, image.shape)
    x, y, w, h = cv2.boundingRect(quad)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, image.shape[1]), min(y + h, image.shape[0])
    crop = image[y0:y1, x0:x1]
    quad = quad - np.float32([x0, y0])
    # Сначала уменьшаем с усреднением: warpPerspective при сильном уменьшении даёт муар на мелкой текстуре
    factor = min(1.0, pw / max(_sides(quad)[0], 1))
    if factor < 0.9:
        crop = cv2.resize(crop, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        quad = quad * factor
    dst = np.float32([(0, 0), (pw - 1, 0), (pw - 1, ph - 1), (0, ph - 1)])
    matrix = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(crop, matrix, (pw, ph), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def is_grayscale(image: np.ndarray) -> bool:
    """Чёрно-белый кадр — камера в ночном режиме с ИК-подсветкой: цвет сравнивать нельзя."""
    small = cv2.resize(image, (160, max(round(160 * image.shape[0] / image.shape[1]), 1)),
                       interpolation=cv2.INTER_AREA)
    saturation = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 1]
    return float(np.mean(saturation > 25)) < 0.005
