"""Тексты уведомлений. Отделены от транспорта, чтобы все каналы писали одинаково."""

import dataclasses
from datetime import datetime

from ..events import (CameraOffline, CameraOnline, Event, PeopleCountChanged, PersonEntered, PersonLeft,
                      SeedsLevelChanged, VehicleArrived, VehicleLeft)
from ..plates.names import KIND_NAMES
from .base import Notification


def format_duration(seconds: float) -> str:
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} ч {minutes} мин"
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


LEVEL_NAMES = {"ok": "семена есть", "low": "семян мало", "empty": "семена закончились", "unknown": "не видно",
               "stopped": "дробилка стоит"}


def _seeds(e: SeedsLevelChanged) -> Notification:
    notification = _seeds_text(e)
    if e.test:
        return dataclasses.replace(notification, title=f"ПРОВЕРКА — {notification.title}",
                                   message=f"{notification.message}\n(это проверка оповещений, не настоящая тревога)")
    return notification


def _seeds_text(e: SeedsLevelChanged) -> Notification:
    where = f" ({', '.join(e.zones)})" if e.zones else ""
    match e.level:
        case "empty" if e.reminder:
            return Notification(title=f"{e.camera}: семена всё ещё не досыпаны",
                                message=f"{_clock(e.ts)} — пусто уже {format_duration(e.duration_s)}",
                                tags=("rotating_light",), priority="urgent", image=e.snapshot)
        case "empty":
            return Notification(title=f"{e.camera}: закончились семена",
                                message=f"{_clock(e.ts)} — в желобе пусто{where}. Досыпьте семена.",
                                tags=("rotating_light",), priority="urgent", image=e.snapshot)
        case "low":
            return Notification(title=f"{e.camera}: семена заканчиваются",
                                message=f"{_clock(e.ts)} — уровень опустился ниже отметки{where}",
                                tags=("warning",), priority="high", image=e.snapshot)
        case "unknown":
            last = LEVEL_NAMES.get(e.last_known or "", "ещё не определялось")
            return Notification(title=f"{e.camera}: не видно желоб",
                                message=(f"{_clock(e.ts)} — уровень семян не определить уже "
                                         f"{format_duration(e.duration_s)}{where}: темно, зону закрыли или камера "
                                         f"сдвинулась. Последнее состояние: {last}"),
                                tags=("eyes",), priority="high", image=e.snapshot)
        case "stopped":
            return Notification(title=f"{e.camera}: дробилка остановлена",
                                message=f"{_clock(e.ts)} — привод не движется. Пока стоит, о семенах не сообщается",
                                tags=("pause_button",), priority="low", image=e.snapshot)
    if e.previous == "stopped":
        return Notification(title=f"{e.camera}: дробилка работает, семена есть",
                            message=f"{_clock(e.ts)} — стояла {format_duration(e.duration_s)}",
                            tags=("arrow_forward",), priority="low", image=e.snapshot)
    if e.previous in ("empty", "low"):
        was = "пусто" if e.previous == "empty" else "мало"
        return Notification(title=f"{e.camera}: семена досыпали",
                            message=f"{_clock(e.ts)} — семена есть. Было {was} {format_duration(e.duration_s)}",
                            tags=("white_check_mark",), image=e.snapshot)
    if e.previous == "unknown":
        return Notification(title=f"{e.camera}: желоб снова виден", message=f"{_clock(e.ts)} — семена есть",
                            tags=("white_check_mark",), priority="low", image=e.snapshot)
    return Notification(title=f"{e.camera}: семена есть", message=f"{_clock(e.ts)} — наблюдение началось",
                        tags=("white_check_mark",), priority="low", image=e.snapshot)


def format_event(event: Event) -> Notification | None:
    match event:
        case SeedsLevelChanged():
            return _seeds(event)
        case PersonEntered(camera=camera, ts=ts, count=count, snapshot=snapshot):
            return Notification(
                title=f"{camera}: человек в кадре",
                message=f"{_clock(ts)} — обнаружено людей: {count}",
                tags=("bust_in_silhouette",),
                priority="high",
                image=snapshot,
            )
        case PersonLeft(camera=camera, ts=ts, duration_s=duration, snapshot=snapshot):
            return Notification(
                title=f"{camera}: человек ушёл",
                message=f"{_clock(ts)} — в кадре никого. Был в кадре {format_duration(duration)}",
                tags=("white_check_mark",),
                image=snapshot,
            )
        case PeopleCountChanged(camera=camera, ts=ts, count=count, previous=previous, snapshot=snapshot):
            return Notification(
                title=f"{camera}: {'кто-то пришёл' if count > previous else 'кто-то ушёл'}",
                message=f"{_clock(ts)} — людей в кадре: {count} (было {previous})",
                tags=("busts_in_silhouette",),
                image=snapshot,
            )
        case VehicleArrived(camera=camera, ts=ts, plate=plate, plate_verified=verified, color=color, kind=kind,
                            snapshot=snapshot):
            if plate is None:
                plate_line = "не распознан"
            else:
                plate_line = plate if verified else f"{plate} (прочитан неуверенно, сверьте по фото)"
            return Notification(
                title=f"{camera}: подъехала машина {plate or ''}".rstrip(),
                message=(f"Номер: {plate_line}\nЦвет: {color}\nТип: {KIND_NAMES.get(kind, kind)}\n"
                         f"Время: {_clock(ts)}"),
                tags=("truck",) if kind in ("truck", "bus") else ("red_car",),
                image=snapshot,
            )
        case VehicleLeft(camera=camera, ts=ts, plate=plate, color=color, kind=kind, duration_s=duration):
            what = plate or f"{color} {KIND_NAMES.get(kind, kind)}"
            return Notification(
                title=f"{camera}: уехала машина {what}",
                message=f"{_clock(ts)} — стояла {format_duration(duration)}",
                tags=("checkered_flag",),
                priority="low",
            )
        case CameraOffline(camera=camera, since=since):
            return Notification(
                title=f"{camera}: нет связи с камерой",
                message=f"Камера недоступна с {_clock(since)}",
                tags=("warning",),
                priority="high",
            )
        case CameraOnline(camera=camera, ts=ts, downtime_s=downtime):
            return Notification(
                title=f"{camera}: камера снова в сети",
                message=f"{_clock(ts)} — связь восстановлена, простой {format_duration(downtime)}",
                tags=("electric_plug",),
            )
    return None
