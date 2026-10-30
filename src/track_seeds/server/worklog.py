"""Журнал работы дробилок для отчётов: сколько работала, сколько без зерна, сколько стояла.

Раз в 5 секунд сервер смотрит состояние каждой дробилки и продлевает текущий интервал (в базе —
раз в 30 секунд), смена состояния закрывает интервал и открывает новый. Пока сервер выключен или
наблюдение за дробилкой выключено, интервалов нет — в отчёте это «нет данных».

Состояния: running — привод крутится, семена есть; idle — крутится, но семена закончились;
stopped — привод стоит; offline — нет связи с камерой; unknown — работает ли, не определить.
"""

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

log = logging.getLogger(__name__)

STATES = ("running", "idle", "stopped", "offline", "unknown")
WORK = {"running", "idle"}
_FLUSH_S = 30
_GAP_S = 30  # пропуск дольше — разрыв: сервер не работал
_MIN_EPISODE_S = 60  # остановки и «без зерна» короче минуты в счёт не идут
_CONTEXT_S = 86400  # что было до начала периода (остановилась ли после работы)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    crusher_id TEXT NOT NULL,
    state      TEXT NOT NULL,
    start      REAL NOT NULL,
    end        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS work_crusher_end ON work (crusher_id, end);
"""

type Interval = tuple[str, float, float]


class WorkLog:
    def __init__(self, data_dir: Path, keep_days: int = 400) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "worklog.db", check_same_thread=False, timeout=10)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._keep_days = keep_days
        self._open: dict[str, list] = {}  # дробилка → [id строки, состояние, начало, конец]
        self._flushed = time.time()
        self._pruned = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- запись ---

    def record(self, crusher_id: str, state: str | None, ts: float) -> None:
        """Состояние дробилки на момент ts; None — не наблюдается (интервал закрывается)."""
        with self._lock:
            current = self._open.get(crusher_id)
            if current is not None and (state != current[1] or ts - current[3] > _GAP_S):
                if ts - current[3] <= _GAP_S:
                    current[3] = ts  # сменилось состояние: прежнее длилось до этого момента
                self._save(current)
                del self._open[crusher_id]
                current = None
            if state is not None:
                if current is None:
                    cursor = self._db.execute("INSERT INTO work (crusher_id, state, start, end) VALUES (?, ?, ?, ?)",
                                              (crusher_id, state, ts, ts))
                    self._open[crusher_id] = [cursor.lastrowid, state, ts, ts]
                else:
                    current[3] = ts
            if ts - self._flushed >= _FLUSH_S:
                for row in self._open.values():
                    self._save(row)
                self._flushed = ts
            self._db.commit()
        if ts - self._pruned > 86400:
            self.prune(ts)

    def _save(self, row: list) -> None:
        self._db.execute("UPDATE work SET end = ? WHERE id = ?", (row[3], row[0]))

    def start(self, states: Callable[[], Iterable[tuple[str, str | None]]], interval_s: float = 5) -> None:
        """Фоновый поток: раз в interval_s записывает states() → (дробилка, состояние)."""

        def run() -> None:
            while not self._stop.wait(interval_s):
                now = time.time()
                try:
                    for crusher_id, state in states():
                        self.record(crusher_id, state, now)
                except Exception:
                    log.exception("Журнал работы: не записалось")

        self._stop.clear()
        self._thread = threading.Thread(target=run, name="worklog", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        with self._lock:
            for row in self._open.values():
                self._save(row)
            self._open.clear()
            self._db.commit()
            self._db.close()

    def prune(self, now: float | None = None) -> None:
        now = now or time.time()
        self._pruned = now
        with self._lock:
            self._db.execute("DELETE FROM work WHERE end < ?", (now - self._keep_days * 86400,))
            self._db.commit()

    # --- чтение ---

    def intervals(self, crusher_id: str, start: float, end: float) -> list[Interval]:
        """Интервалы, задевающие [start, end), по порядку; текущий — с концом из памяти."""
        with self._lock:
            rows = self._db.execute("SELECT id, state, start, end FROM work WHERE crusher_id = ? AND end >= ? "
                                    "AND start < ? ORDER BY start", (crusher_id, start, end)).fetchall()
            current = self._open.get(crusher_id)
        return [(state, s, current[3] if current and current[0] == row_id else e) for row_id, state, s, e in rows]


# --- отчёты ---

def period_bounds(period: str, day: date) -> tuple[datetime, datetime, list[datetime]]:
    """Начало и конец периода (местное время сервера) и границы столбцов: день — по часам, неделя и месяц — по дням."""
    if period == "day":
        edges = [datetime.combine(day, dtime(hour)).astimezone() for hour in range(24)]
        edges.append(datetime.combine(day + timedelta(days=1), dtime()).astimezone())
        return edges[0], edges[-1], edges
    if period == "week":
        start = day - timedelta(days=day.weekday())
        days = 7
    elif period == "month":
        start = day.replace(day=1)
        days = ((start.replace(day=28) + timedelta(days=4)).replace(day=1) - start).days
    else:
        raise ValueError(f"Неизвестный период: {period}")
    edges = [datetime.combine(start + timedelta(days=i), dtime()).astimezone() for i in range(days + 1)]
    return edges[0], edges[-1], edges


def shift(period: str, day: date, step: int) -> date:
    """Тот же день в соседнем периоде."""
    if period == "day":
        return day + timedelta(days=step)
    if period == "week":
        return day + timedelta(weeks=step)
    month = day.month - 1 + step
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, 1)


def _merge(intervals: list[Interval]) -> list[Interval]:
    """Соседние интервалы одного состояния (без разрыва) — в один."""
    merged: list[list] = []
    for state, s, e in intervals:
        if merged and merged[-1][0] == state and s - merged[-1][2] <= _GAP_S:
            merged[-1][2] = max(merged[-1][2], e)
        else:
            merged.append([state, s, e])
    return [(state, s, e) for state, s, e in merged]


def _episodes(merged: list[Interval], kinds: set[str]) -> list[list[float]]:
    """Сплошные отрезки из состояний kinds (работа — крутится с семенами и без них); прерывает любое другое
    состояние или разрыв."""
    episodes: list[list[float]] = []
    previous: Interval | None = None
    for state, s, e in merged:
        if state in kinds:
            if previous is not None and previous[0] in kinds and s - previous[2] <= _GAP_S:
                episodes[-1][1] = e
            else:
                episodes.append([s, e])
        previous = (state, s, e)
    return episodes


def summarize(intervals: list[Interval], start: float, end: float, now: float, edges: list[float],
              timeline: bool = False) -> dict:
    """Итоги за [start, end) по уже прошедшему времени: часы по состояниям и по столбцам, остановки.
    intervals — с запасом до start: по ним видно, что было перед остановкой в начале периода."""
    stop = min(end, now)

    def inside(s: float, e: float) -> bool:
        return min(e, stop) > max(s, start)

    def clip(s: float, e: float) -> list[float]:
        return [max(s, start), min(e, stop)]

    totals = dict.fromkeys(STATES, 0.0)
    buckets = [dict.fromkeys(STATES, 0.0) for _ in edges[:-1]]
    for state, s, e in intervals:
        if not inside(s, e):
            continue
        s, e = clip(s, e)
        totals[state] += e - s
        for i, bucket in enumerate(buckets):
            overlap = min(e, edges[i + 1]) - max(s, edges[i])
            if overlap > 0:
                bucket[state] += overlap
    totals["nodata"] = max(0.0, stop - start - sum(totals.values()))
    for i, bucket in enumerate(buckets):
        elapsed = max(0.0, min(edges[i + 1], stop) - edges[i])
        bucket["nodata"] = max(0.0, elapsed - sum(bucket.values()))

    merged = _merge(intervals)
    work = [clip(s, e) for s, e in _episodes(merged, WORK) if inside(s, e)]

    def episodes(state: str) -> tuple[list[list[float]], int]:
        """Отрезки состояния за период (не короче минуты) и сколько раз оно наступило за период после работы."""
        spans, began = [], 0
        for i, (current, s, e) in enumerate(merged):
            if current != state or not inside(s, e):
                continue
            if min(e, stop) - max(s, start) >= _MIN_EPISODE_S:
                spans.append(clip(s, e))
            after_work = i > 0 and merged[i - 1][0] in WORK and s - merged[i - 1][2] <= _GAP_S
            began += start <= s < stop and e - s >= _MIN_EPISODE_S and (after_work or state == "idle")
        return spans, began

    stops, stop_count = episodes("stopped")
    runouts, runout_count = episodes("idle")
    working_now = now < end and bool(work) and stop - work[-1][1] <= _GAP_S
    result = {
        "totals": {k: round(v) for k, v in totals.items()},
        "buckets": [{k: round(v) for k, v in b.items()} for b in buckets],
        "stops": stop_count,
        "runouts": runout_count,
        "longest_work": round(max((e - s for s, e in work), default=0)),
        "longest_stop": round(max((e - s for s, e in stops), default=0)),
        "first_work": work[0][0] if work else None,
        "last_work": work[-1][1] if work and not working_now else None,  # работает сейчас — конца ещё нет
    }
    if timeline:
        result["timeline"] = [[state, *(round(x, 1) for x in clip(s, e))] for state, s, e in merged if inside(s, e)]
        result["stop_list"] = [[round(s, 1), round(e, 1)] for s, e in stops]
        result["runout_list"] = [[round(s, 1), round(e, 1)] for s, e in runouts]
    return result


def report(worklog: WorkLog, crushers: list[tuple[str, str]], period: str, day: date, now: float) -> dict:
    """Отчёт за день / неделю / месяц, в который попадает day."""
    _, _, edges = period_bounds(period, day)
    stamps = [e.timestamp() for e in edges]
    previous, following = shift(period, day, -1), shift(period, day, 1)
    return {
        "period": period,
        "date": day.isoformat(),
        "start": stamps[0],
        "end": stamps[-1],
        "now": now,
        "edges": stamps,
        "prev": previous.isoformat(),
        "next": following.isoformat() if period_bounds(period, following)[0].timestamp() <= now else None,
        "crushers": [
            {"id": crusher_id, "name": name,
             **summarize(worklog.intervals(crusher_id, stamps[0] - _CONTEXT_S, stamps[-1]), stamps[0], stamps[-1],
                         now, stamps, timeline=period == "day")}
            for crusher_id, name in crushers
        ],
    }


def today(worklog: WorkLog, crusher_id: str, now: float) -> dict:
    """Итоги с начала суток — для виджета."""
    _, _, edges = period_bounds("day", datetime.fromtimestamp(now).date())
    start, end = edges[0].timestamp(), edges[-1].timestamp()
    totals = summarize(worklog.intervals(crusher_id, start - _CONTEXT_S, end), start, end, now, [start, end])
    return totals["totals"] | {"stops": totals["stops"]}
