"""Доменные события. Анализаторы их публикуют, подписчики (уведомления и т.п.) обрабатывают.

События содержат только простые данные (снимок — уже JPEG-байты), поэтому
шину при необходимости можно заменить на внешнюю (MQTT/Redis) без правки анализаторов.
"""

import time
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True, kw_only=True)
class Event:
    camera: str
    ts: float = field(default_factory=time.time)
    camera_id: str = field(default="", repr=False)  # id камеры в веб-интерфейсе (заполняет сервер)
    id: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)
    quiet: bool = field(default=False, repr=False)  # только в журнал событий, без уведомлений


@dataclass(frozen=True, kw_only=True)
class PersonEntered(Event):
    count: int
    snapshot: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class PersonLeft(Event):
    duration_s: float
    snapshot: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class PeopleCountChanged(Event):
    count: int
    previous: int
    snapshot: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class VehicleArrived(Event):
    vehicle_id: str
    plate: str | None  # для людей: С484НХ 70; None — не прочитан
    plate_verified: bool  # номер совпал с форматом и прочитан несколько раз
    plate_confidence: float
    color: str
    kind: str  # класс детектора: car, truck, bus, motorcycle
    snapshot: bytes | None = field(default=None, repr=False)
    plate_image: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, kw_only=True)
class VehicleLeft(Event):
    vehicle_id: str
    plate: str | None
    color: str
    kind: str
    duration_s: float


@dataclass(frozen=True, kw_only=True)
class CameraOffline(Event):
    since: float


@dataclass(frozen=True, kw_only=True)
class CameraOnline(Event):
    downtime_s: float


@dataclass(frozen=True, kw_only=True)
class SeedsLevelChanged(Event):
    """Уровень семян в бункере: ok — есть, low — мало, empty — закончились, unknown — не видно,
    stopped — дробилка стоит (привод не движется), о семенах не сообщается."""

    level: str
    previous: str | None  # None — первое определение после запуска
    # смена — сколько длилось предыдущее состояние; напоминание — сколько уже длится текущее;
    # unknown — сколько уже не удаётся определить
    duration_s: float
    reminder: int = 0  # номер напоминания «всё ещё пусто»; 0 — само изменение
    zones: tuple[str, ...] = ()  # пустые зоны (для empty/low) или невидимые (для unknown)
    last_known: str | None = None  # для unknown: последнее определённое состояние
    test: bool = False  # проверка оповещений из веб-интерфейса, а не настоящая тревога
    snapshot: bytes | None = field(default=None, repr=False)
    frame: bytes | None = field(default=None, repr=False)  # кадр без разметки: из него можно сделать эталон
