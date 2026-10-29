from collections.abc import Callable

from ..config import Settings
from .base import Notification, Notifier
from .log import LogNotifier
from .ntfy import NtfyNotifier
from .service import NotificationService

# Новый канал (Telegram, e-mail, ...) = класс с методом send() + запись здесь
CHANNELS: dict[str, Callable[[Settings], Notifier]] = {
    "log": lambda _: LogNotifier(),
    "ntfy": lambda s: NtfyNotifier(s.ntfy),
}


def build_notifiers(settings: Settings) -> list[Notifier]:
    unknown = set(settings.notify.channels) - CHANNELS.keys()
    if unknown:
        raise ValueError(f"Неизвестные каналы NOTIFY_CHANNELS: {', '.join(sorted(unknown))} "
                         f"(доступны: {', '.join(CHANNELS)})")
    return [CHANNELS[name](settings) for name in settings.notify.channels]


__all__ = ["CHANNELS", "LogNotifier", "Notification", "NotificationService", "Notifier", "NtfyNotifier",
           "build_notifiers"]
