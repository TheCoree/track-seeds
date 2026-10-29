"""Внутрипроцессная шина событий (pub/sub)."""

import logging
import queue
import threading
from collections.abc import Callable

from .events import Event

log = logging.getLogger(__name__)

_STOP = object()


class EventBus:
    """Обработчики выполняются в отдельном потоке, чтобы медленный подписчик
    (например, HTTP-уведомление с ретраями) не тормозил обработку видео."""

    def __init__(self) -> None:
        self._handlers: list[tuple[type[Event], Callable[[Event], None]]] = []
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread: threading.Thread | None = None

    def subscribe[E: Event](self, event_type: type[E], handler: Callable[[E], None]) -> None:
        self._handlers.append((event_type, handler))  # type: ignore[arg-type]

    def publish(self, event: Event) -> None:
        log.info("Событие: %s", event)
        self._queue.put(event)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="event-bus", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30) -> None:
        """Останавливает шину, дождавшись обработки уже опубликованных событий."""
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        while (event := self._queue.get()) is not _STOP:
            self._dispatch(event)  # type: ignore[arg-type]

    def _dispatch(self, event: Event) -> None:
        for event_type, handler in self._handlers:
            if isinstance(event, event_type):
                try:
                    handler(event)
                except Exception:
                    log.exception("Ошибка в обработчике %r для %s", handler, type(event).__name__)
