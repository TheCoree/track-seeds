from collections.abc import Callable
from pathlib import Path

from ..config import DetectorConfig
from .base import Detection, Detector, FrameCachedDetector, draw_detections, iou, merge_detections
from .models import CATALOG, download_model
from .tiling import TiledDetector, tile_grid
from .yolox import YoloxDetector


def _build_yolox(settings: DetectorConfig) -> Detector:
    if settings.model in CATALOG:
        path = download_model(settings.model, settings.models_dir)
        input_size = settings.input_size or CATALOG[settings.model].input_size
    else:
        path = Path(settings.model)
        if not path.is_file():
            raise FileNotFoundError(f"Модель не найдена: {path} (доступные имена: {', '.join(CATALOG)})")
        if not settings.input_size:
            raise ValueError("Для своей модели задайте DETECTOR_INPUT_SIZE")
        input_size = settings.input_size
    return YoloxDetector(path, input_size, settings.confidence, settings.nms, settings.containment)


# Новый бэкенд = функция-фабрика + запись здесь + значение в DetectorConfig.backend
BACKENDS: dict[str, Callable[[DetectorConfig], Detector]] = {
    "yolox": _build_yolox,
}


def build_detector(settings: DetectorConfig) -> Detector:
    detector = BACKENDS[settings.backend](settings)
    if settings.tiling:
        detector = TiledDetector(detector, settings.nms, settings.containment)
    return detector


__all__ = ["BACKENDS", "CATALOG", "Detection", "Detector", "FrameCachedDetector", "TiledDetector", "build_detector", "download_model",
           "draw_detections", "iou", "merge_detections", "tile_grid"]
