"""Поиск и чтение номерного знака.

Детектор номеров — open-image-models (YOLOv9), OCR — fast-plate-ocr; обе MIT, ONNX Runtime.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import PlatesConfig
from .russian import clean, parse_ru

log = logging.getLogger(__name__)

_PROVIDERS = ["CPUExecutionProvider"]


@dataclass(frozen=True, slots=True)
class PlateRead:
    """Прочтение, прошедшее все проверки: размер номера, уверенность символов, формат."""

    text: str  # ключ для сравнения прочтений (латиница без пробелов)
    display: str  # для людей: С484НХ 70
    confidence: float  # средняя уверенность OCR по символам
    corrections: int  # сколько символов исправлено по формату
    box: tuple[int, int, int, int]  # рамка номера в координатах кадра
    image: np.ndarray  # вырезанный номер (BGR)


def plate_models_dir(models_dir: Path) -> Path:
    return models_dir / "plates"


@contextmanager
def _quiet_libraries() -> Iterator[None]:
    """Библиотеки пишут INFO в корневой логгер («Skipping download…», «Using ONNX Runtime…»)."""
    previous = logging.root.manager.disable
    logging.disable(logging.INFO)
    try:
        yield
    finally:
        logging.disable(previous)


def download_plate_models(settings: PlatesConfig, models_dir: Path) -> tuple[Path, Path, Path]:
    """Скачивает модели детектора номеров и OCR в models/plates; возвращает пути (детектор, OCR, конфиг OCR)."""
    from fast_plate_ocr.inference.hub import download_model as download_ocr
    from open_image_models.detection.core.hub import download_model as download_detector

    target = plate_models_dir(models_dir)
    with _quiet_libraries():
        detector_path = download_detector(settings.detector_model, save_directory=target / settings.detector_model)
        ocr_path, ocr_config = download_ocr(settings.ocr_model, save_directory=target / settings.ocr_model)
    return detector_path, ocr_path, ocr_config


class PlateReader:
    def __init__(self, settings: PlatesConfig, models_dir: Path) -> None:
        from fast_plate_ocr import LicensePlateRecognizer
        from open_image_models import create_detector
        from open_image_models.detection.core.hub import DETECTION_MODELS

        detector_path, ocr_path, ocr_config = download_plate_models(settings, models_dir)
        spec = DETECTION_MODELS[settings.detector_model]
        with _quiet_libraries():
            self._detector = create_detector(detector_path, backend=spec.backend, class_labels=spec.class_labels,
                                             conf_thresh=settings.plate_confidence, providers=_PROVIDERS)
            self._ocr = LicensePlateRecognizer(onnx_model_path=ocr_path, plate_config_path=ocr_config,
                                               providers=_PROVIDERS)
        self._grayscale = self._ocr.config.image_color_mode == "grayscale"
        self._format = settings.format
        self._min_width = settings.min_plate_width
        self._min_char_confidence = settings.min_char_confidence

    def read(self, image: np.ndarray, region: tuple[int, int, int, int] | None = None) -> PlateRead | None:
        """Ищет номер внутри region (рамка машины) или на всём изображении и читает его.

        None — номера нет, он слишком мелкий, прочитан неуверенно или не похож на номер.
        Мелкий или размытый номер OCR всё равно «читает», выдавая правдоподобный мусор,
        поэтому лучше не прочитать ничего, чем прочитать неправильно.
        """
        ox, oy = 0, 0
        if region is not None:
            ox, oy, w, h = region
            image = image[oy:oy + h, ox:ox + w]
        if image.size == 0:
            return None

        plates = [p for p in self._detector.predict(image) if p.bounding_box.x2 - p.bounding_box.x1 >= self._min_width]
        if not plates:
            return None
        plate = max(plates, key=lambda p: p.confidence)
        b = plate.bounding_box
        x1, y1 = max(int(b.x1), 0), max(int(b.y1), 0)
        x2, y2 = min(int(b.x2), image.shape[1]), min(int(b.y2), image.shape[0])
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return None

        ocr_input = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY if self._grayscale else cv2.COLOR_BGR2RGB)
        prediction = self._ocr.run(ocr_input, return_confidence=True)[0]
        raw = prediction.plate
        probs = prediction.char_probs[:len(raw)] if prediction.char_probs is not None else np.zeros(0)
        if not len(raw) or len(probs) < len(raw) or probs.min() < self._min_char_confidence:
            return None
        confidence = float(probs.mean())
        box = (x1 + ox, y1 + oy, x2 - x1, y2 - y1)

        if self._format == "ru":
            parsed = parse_ru(raw)
            if parsed is None:
                return None
            return PlateRead(parsed.text, parsed.display, confidence, parsed.corrections, box, crop.copy())
        text = clean(raw)
        return PlateRead(text, text, confidence, 0, box, crop.copy()) if len(text) >= 4 else None


def vote(reads: list[PlateRead]) -> tuple[PlateRead | None, int]:
    """Лучшее прочтение по нескольким кадрам и сколько раз его прочитали так же.

    Побеждает текст с наибольшей суммой уверенностей (прочтения с исправлениями весят меньше).
    """
    pool = reads
    if not pool:
        return None, 0
    scores: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for r in pool:
        scores[r.text] += r.confidence * (1 - 0.25 * r.corrections)
        counts[r.text] += 1
    best_text = max(scores, key=scores.__getitem__)
    best = max((r for r in pool if r.text == best_text), key=lambda r: r.confidence)
    return best, counts[best_text]
