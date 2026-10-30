"""Состояние дробилок между перезапусками сервера.

Обновление прода пересоздаёт контейнер: без этого файла после него было бы «семена есть уже
1 секунду», хотя до обновления — 2 часа, и первая тревога после запуска шла бы как новая.
Раз в 5 секунд и при остановке сервер записывает, что знает о каждой дробилке (уровень, работает ли,
с какого времени), в DATA_DIR/resume.json; при запуске анализ продолжает с этого, если перерыв
короткий (analyzers.seeds.RESUME_MAX_GAP_S).
"""

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .store import write_atomic

log = logging.getLogger(__name__)

_SAVE_EVERY_S = 5.0

type Snapshots = dict[str, dict]


class ResumeFile:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "resume.json"
        self._written: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def load(self) -> Snapshots:
        """Дробилка → её состояние. Нет файла или он испорчен — начинаем с нуля (это не ошибка работы)."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("Состояние до перезапуска не прочиталось (%s) — начинаем заново", e)
            return {}
        crushers = data.get("crushers") if isinstance(data, dict) else None
        return {k: v for k, v in crushers.items() if isinstance(v, dict)} if isinstance(crushers, dict) else {}

    def save(self, snapshots: Snapshots) -> None:
        text = json.dumps({"format": 1, "crushers": snapshots}, ensure_ascii=False) + "\n"
        with self._lock:
            if text == self._written:
                return
            try:
                write_atomic(self.path, text, backup=False)
                self._written = text
            except OSError as e:
                log.warning("Состояние дробилок не записалось: %s", e)

    def start(self, collect: Callable[[], Snapshots]) -> None:
        def loop() -> None:
            while not self._stop.wait(_SAVE_EVERY_S):
                try:
                    self.save(collect())
                except Exception:
                    log.exception("Состояние дробилок не записалось")

        self._stop.clear()
        self._thread = threading.Thread(target=loop, name="resume", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
