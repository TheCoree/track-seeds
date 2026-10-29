"""Устойчивое состояние зоны: «есть семена» / «пусто» подтверждается за окно времени."""

from collections import deque
from enum import StrEnum


class Reading(StrEnum):
    """Что видно в зоне на одном кадре."""

    COVERED = "covered"  # семена есть
    UNCOVERED = "uncovered"  # пусто
    UNKNOWN = "unknown"  # не определить: темно, закрыто человеком, непохоже на эталоны


class ZoneDebounce:
    """Состояние меняется, только если последние after_s секунд зона показывает одно и то же.

    Окно делится на три части, и в каждой доля согласных кадров должна быть не меньше
    agreement: редкие ошибочные кадры не мешают, а смена состояния подтверждается примерно
    через after_s секунд, а не раньше (доля по всему окну набралась бы уже к его середине).

    Кадры UNKNOWN не голосуют: если на минуту зону загородил человек, состояние
    просто держится. Но и решение без данных не принимается: определённых кадров
    должно быть не меньше половины, и в каждой трети окна — хотя бы один.
    """

    def __init__(self, empty_after_s: float, refill_after_s: float, agreement: float) -> None:
        self._empty_after_s = empty_after_s
        self._refill_after_s = refill_after_s
        self._agreement = agreement
        self._readings: deque[tuple[float, Reading]] = deque()
        self._started: float | None = None
        self.stable: Reading | None = None  # COVERED / UNCOVERED; None — ещё не определено
        self.stable_since: float | None = None
        self.last_known: float | None = None  # когда последний раз удалось определить

    def update(self, ts: float, reading: Reading) -> bool:
        """Добавляет кадр; True — устойчивое состояние изменилось."""
        if self._started is None:
            self._started = ts
        if reading is not Reading.UNKNOWN:
            self.last_known = ts
        self._readings.append((ts, reading))
        horizon = ts - max(self._empty_after_s, self._refill_after_s)
        while self._readings[0][0] < horizon:
            self._readings.popleft()

        # Пока состояние не определено (старт), сначала проверяем более короткое окно — «есть семена»
        candidates = {Reading.COVERED: self._refill_after_s, Reading.UNCOVERED: self._empty_after_s}
        for target, window in candidates.items():
            if target is not self.stable and self._confirmed(ts, target, window):
                self.stable, self.stable_since = target, ts
                return True
        return False

    def inherit(self, old: "ZoneDebounce") -> None:
        """Продолжить с состоянием старого экземпляра (настройки поменяли — тревога не повторяется)."""
        self._readings = old._readings
        self._started = old._started
        self.stable, self.stable_since, self.last_known = old.stable, old.stable_since, old.last_known

    def blind_for(self, ts: float) -> float:
        """Сколько секунд подряд зону не удаётся определить."""
        since = self.last_known if self.last_known is not None else self._started
        return ts - since if since is not None else 0.0

    def _confirmed(self, ts: float, target: Reading, window: float) -> bool:
        if self._started is None or ts - self._started < window:
            return False
        start = ts - window
        in_window = [(t, r) for t, r in self._readings if t >= start]
        known = [(t, r) for t, r in in_window if r is not Reading.UNKNOWN]
        if len(known) < 3 or len(known) < 0.5 * len(in_window):
            return False
        thirds: list[list[Reading]] = [[], [], []]
        for t, r in known:
            thirds[min(int((t - start) * 3 / window), 2)].append(r)
        return all(part and sum(r is target for r in part) >= self._agreement * len(part) for part in thirds)
