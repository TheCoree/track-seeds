from collections.abc import Callable

from ..config import Settings
from ..detection import Detector, FrameCachedDetector, build_detector
from .base import Analyzer
from .presence import PresenceAnalyzer, PresenceTracker, Transition


def _presence(settings: Settings, detector: Detector) -> Analyzer:
    return PresenceAnalyzer(detector, settings.presence, settings.camera.name)


def _plates(settings: Settings, detector: Detector) -> Analyzer:
    # ONNX Runtime и модели номеров грузятся, только если пресет включён
    from ..plates import PlateReader
    from .vehicles import VehicleAnalyzer

    open_visit = None
    if settings.plates.journal:
        from ..journal import find_open_visit

        data_dir = settings.app.data_dir
        open_visit = lambda camera, plate: find_open_visit(data_dir, camera, plate)  # noqa: E731
    reader = PlateReader(settings.plates, settings.detector.models_dir)
    return VehicleAnalyzer(detector, reader, settings.plates, settings.camera.name, open_visit=open_visit)


def _seeds(settings: Settings, detector: Detector | None) -> Analyzer:
    from .seeds import SeedsAnalyzer

    return SeedsAnalyzer(settings.seeds, settings.camera.name, settings.app.data_dir, detector)


# Пресеты для ANALYZERS. Новый анализатор = фабрика + запись здесь (+ PRESETS для веб-интерфейса).
ANALYZERS: dict[str, Callable[[Settings, Detector], Analyzer]] = {
    "presence": _presence,
    "plates": _plates,
    "seeds": _seeds,  # type: ignore[dict-item]
}

# Названия и описания для веб-интерфейса
PRESETS: dict[str, tuple[str, str]] = {
    "seeds": ("Дробилка: семена", "Следит за бункером: семена заканчиваются, закончились, досыпали. "
                                  "Людей не отслеживает"),
    "presence": ("Люди в кадре", "Человек появился / ушёл, изменилось число людей"),
    "plates": ("Номера машин", "Машина подъехала и остановилась: номер, цвет, тип; журнал заездов"),
}


def _needs_detector(settings: Settings) -> bool:
    names = set(settings.app.analyzers)
    return bool(names & {"presence", "plates"}) or ("seeds" in names and settings.seeds.ignore_people)


def build_analyzers(settings: Settings) -> list[Analyzer]:
    names = settings.app.analyzers
    unknown = set(names) - ANALYZERS.keys()
    if unknown:
        raise ValueError(f"Неизвестные ANALYZERS: {', '.join(sorted(unknown))} (доступны: {', '.join(ANALYZERS)})")
    if not names:
        return []
    # Детектор объектов (YOLOX) грузится, только если он кому-то нужен; на один кадр — одна детекция на всех
    detector = FrameCachedDetector(build_detector(settings.detector)) if _needs_detector(settings) else None
    return [ANALYZERS[name](settings, detector) for name in names]  # type: ignore[arg-type]


__all__ = ["ANALYZERS", "PRESETS", "Analyzer", "PresenceAnalyzer", "PresenceTracker", "Transition",
           "build_analyzers"]
