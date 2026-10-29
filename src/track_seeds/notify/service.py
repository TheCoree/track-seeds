import dataclasses
import logging
import time
from collections.abc import Callable, Sequence

from ..bus import EventBus
from ..config import NotifyConfig
from ..events import Event
from .base import Notification, Notifier
from .messages import format_event

log = logging.getLogger(__name__)


class NotificationService:
    """Подписчик шины: превращает события в уведомления и рассылает по всем каналам."""

    def __init__(self, notifiers: Sequence[Notifier], settings: NotifyConfig,
                 formatter: Callable[[Event], Notification | None] = format_event,
                 on_result: Callable[[str, str, bool, str | None], None] | None = None) -> None:
        self._notifiers = list(notifiers)
        self._settings = settings
        self._format = formatter
        self._on_result = on_result  # (id события, канал, доставлено, ошибка) — для журнала в вебе

    @property
    def notifiers(self) -> list[Notifier]:
        return self._notifiers

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(Event, self.handle)

    def handle(self, event: Event) -> None:
        if event.quiet:
            return
        notification = self._format(event)
        if notification is None:
            return
        if not self._settings.attach_snapshot and notification.image:
            notification = dataclasses.replace(notification, image=None)
        for notifier in self._notifiers:
            error = self.deliver(notifier, notification)
            if self._on_result is not None:
                self._on_result(event.id, notifier.name, error is None, error)

    def send(self, notifier: Notifier, notification: Notification) -> bool:
        return self.deliver(notifier, notification) is None

    def deliver(self, notifier: Notifier, notification: Notification) -> str | None:
        """Отправляет с повторами; None — доставлено, иначе текст последней ошибки."""
        error = ""
        for attempt in range(1, self._settings.retries + 1):
            try:
                notifier.send(notification)
                return None
            except Exception as e:
                error = str(e) or type(e).__name__
                log.warning("%s: ошибка отправки (попытка %d/%d): %s",
                            notifier.name, attempt, self._settings.retries, e)
                if attempt < self._settings.retries:
                    time.sleep(self._settings.retry_delay_s)
        log.error("%s: уведомление «%s» не доставлено", notifier.name, notification.title)
        return error
