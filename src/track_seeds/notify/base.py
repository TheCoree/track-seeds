from dataclasses import dataclass, field
from typing import Protocol

from ..config import Priority


@dataclass(frozen=True, kw_only=True)
class Notification:
    title: str
    message: str
    tags: tuple[str, ...] = ()  # для ntfy — имена эмодзи: https://docs.ntfy.sh/emojis/
    priority: Priority = "default"
    image: bytes | None = field(default=None, repr=False)  # JPEG


class Notifier(Protocol):
    name: str

    def send(self, notification: Notification) -> None:
        """Отправляет уведомление; при ошибке бросает исключение (ретраи — на стороне сервиса)."""
        ...
