import logging

from .base import Notification

log = logging.getLogger("track_seeds.notify")


class LogNotifier:
    """Пишет уведомления в лог — для разработки без отправки наружу."""

    name = "log"

    def send(self, notification: Notification) -> None:
        image = f" [снимок {len(notification.image) // 1024} КБ]" if notification.image else ""
        log.warning("🔔 %s — %s%s", notification.title, notification.message, image)
