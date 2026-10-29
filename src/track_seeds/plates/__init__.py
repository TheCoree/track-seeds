"""Номера машин: трекинг, остановка, чтение госномера, цвет."""

from .color import estimate_color
from .names import KIND_NAMES
from .reader import PlateRead, PlateReader, download_plate_models, vote
from .russian import RuPlate, parse_ru
from .tracker import VehicleTrack, VehicleTracker

__all__ = ["KIND_NAMES", "PlateRead", "PlateReader", "RuPlate", "VehicleTrack", "VehicleTracker",
           "download_plate_models", "estimate_color", "parse_ru", "vote"]
