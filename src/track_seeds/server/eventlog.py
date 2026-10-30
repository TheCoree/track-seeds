"""Журнал событий для веб-интерфейса: SQLite + снимки, результаты доставки уведомлений."""

import dataclasses
import json
import logging
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..bus import EventBus
from ..events import Event
from ..notify.messages import format_event

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    uid       TEXT UNIQUE NOT NULL,
    ts        REAL NOT NULL,
    camera_id TEXT NOT NULL,
    camera    TEXT NOT NULL,
    type      TEXT NOT NULL,
    severity  TEXT NOT NULL,
    title     TEXT NOT NULL,
    message   TEXT NOT NULL,
    speech    TEXT,
    snapshot  TEXT,
    frame     TEXT,
    data      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_camera ON events (camera_id, ts);
CREATE TABLE IF NOT EXISTS deliveries (
    event_uid TEXT NOT NULL,
    ts        REAL NOT NULL,
    channel   TEXT NOT NULL,
    ok        INTEGER NOT NULL,
    error     TEXT
);
CREATE INDEX IF NOT EXISTS deliveries_event ON deliveries (event_uid);
"""

_SEVERITY = {"urgent": "alert", "high": "warning"}

# Вид события — для фильтра в журнале: что случилось, а не каким классом записано
KINDS = ("empty", "reminder", "low", "refill", "stopped", "started", "unknown", "offline", "online", "test", "other")


def event_kind(type_name: str, data: dict) -> str:
    """empty — закончились, reminder — «всё ещё пусто», low — заканчиваются, refill — семена есть (досыпали),
    stopped / started — дробилка остановилась / запустилась, unknown — не видно желоб,
    offline / online — связь с камерой, test — проверка оповещений."""
    if data.get("test"):
        return "test"
    if type_name == "CameraOffline":
        return "offline"
    if type_name == "CameraOnline":
        return "online"
    if type_name != "SeedsLevelChanged":
        return "other"
    match data.get("level"):
        case "empty":
            return "reminder" if data.get("reminder") else "empty"
        case "ok":
            return "started" if data.get("previous") == "stopped" else "refill"
        case "low" | "stopped" | "unknown" as level:
            return level
    return "other"


class EventLog:
    """Подписчик шины: пишет каждое событие (и тихие тоже) со снимками в DATA_DIR/events/."""

    def __init__(self, data_dir: Path, keep_days: int = 30,
                 speech: Callable[[Event], str] | None = None) -> None:
        self._dir = data_dir
        self._files = data_dir / "events"
        self._keep_days = keep_days
        self._speech = speech
        self._lock = threading.Lock()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(data_dir / "events.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._migrate()
        self._last_prune = 0.0

    def _migrate(self) -> None:
        """Столбец kind (вид события) — у журналов, записанных до него, вычисляется из сохранённых данных."""
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(events)")}
        if "kind" not in columns:
            self._db.execute("ALTER TABLE events ADD COLUMN kind TEXT")
        rows = self._db.execute("SELECT seq, type, data FROM events WHERE kind IS NULL").fetchall()
        self._db.executemany("UPDATE events SET kind = ? WHERE seq = ?",
                             [(event_kind(r["type"], json.loads(r["data"])), r["seq"]) for r in rows])
        self._db.execute("CREATE INDEX IF NOT EXISTS events_kind ON events (kind, seq)")
        self._db.commit()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(Event, self.record)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def record(self, event: Event) -> None:
        notification = format_event(event)
        title = notification.title if notification else type(event).__name__
        message = notification.message if notification else ""
        severity = _SEVERITY.get(notification.priority, "info") if notification else "info"
        day = datetime.fromtimestamp(event.ts).strftime("%Y-%m-%d")
        snapshot = self._save(f"{day}/{event.id}.jpg", getattr(event, "snapshot", None))
        frame = self._save(f"{day}/{event.id}_frame.jpg", getattr(event, "frame", None))
        data = {f.name: value for f in dataclasses.fields(event)
                if not isinstance(value := getattr(event, f.name), bytes)}
        speech = self._speech(event) if self._speech else ""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO events (uid, ts, camera_id, camera, type, kind, severity, title, message, speech, "
                "snapshot, frame, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.ts, event.camera_id, event.camera, type(event).__name__,
                 event_kind(type(event).__name__, data), severity, title, message,
                 speech or None, snapshot, frame, json.dumps(data, ensure_ascii=False, default=str)),
            )
            self._db.commit()
        if time.time() - self._last_prune > 6 * 3600:
            self.prune()

    def delivery(self, event_uid: str | None, channel: str, ok: bool, error: str | None) -> None:
        if not event_uid:
            return
        with self._lock:
            self._db.execute("INSERT INTO deliveries (event_uid, ts, channel, ok, error) VALUES (?, ?, ?, ?, ?)",
                             (event_uid, time.time(), channel, ok, error))
            self._db.commit()

    def _save(self, relative: str, data: bytes | None) -> str | None:
        if not data:
            return None
        path = self._files / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path.relative_to(self._dir))

    def query(self, camera_id: str | None = None, after: int | None = None, before: int | None = None,
              types: list[str] | None = None, limit: int = 100, tests: bool = True,
              kinds: list[str] | None = None) -> list[dict]:
        """tests=False — без проверок оповещений из веб-интерфейса (клиентам их не показываем);
        kinds — только эти виды событий (KINDS)."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[object] = []
        sql, params = self._filter(sql, params, camera_id, tests)
        if kinds:
            sql += f" AND kind IN ({', '.join('?' * len(kinds))})"
            params += kinds
        if after is not None:
            sql += " AND seq > ?"
            params.append(after)
        if before is not None:
            sql += " AND seq < ?"
            params.append(before)
        if types:
            sql += f" AND type IN ({', '.join('?' * len(types))})"
            params += types
        # новые события, пришедшие после after, — по возрастанию; история — от новых к старым
        sql += " ORDER BY seq " + ("ASC" if after is not None else "DESC") + " LIMIT ?"
        params.append(min(limit, 500))
        with self._lock:
            rows = [dict(r) for r in self._db.execute(sql, params)]
            uids = [r["uid"] for r in rows]
            deliveries: dict[str, list[dict]] = {}
            if uids:
                for d in self._db.execute(
                        f"SELECT * FROM deliveries WHERE event_uid IN ({', '.join('?' * len(uids))}) ORDER BY ts",
                        uids):
                    deliveries.setdefault(d["event_uid"], []).append(
                        {"channel": d["channel"], "ok": bool(d["ok"]), "error": d["error"], "ts": d["ts"]})
        for row in rows:
            row["data"] = json.loads(row["data"])
            row["deliveries"] = deliveries.get(row["uid"], [])
        return rows

    @staticmethod
    def _filter(sql: str, params: list[object], camera_id: str | None, tests: bool) -> tuple[str, list[object]]:
        if not tests:
            sql += " AND kind != 'test'"
        if camera_id:
            sql += " AND camera_id = ?"
            params.append(camera_id)
        return sql, params

    def counts(self, camera_id: str | None = None, tests: bool = True) -> dict[str, int]:
        """Сколько событий каждого вида в журнале (за срок хранения) — для фильтра."""
        sql, params = self._filter("SELECT kind, COUNT(*) FROM events WHERE 1=1", [], camera_id, tests)
        with self._lock:
            return {kind: n for kind, n in self._db.execute(sql + " GROUP BY kind", params)}

    def get(self, uid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM events WHERE uid = ?", (uid,)).fetchone()
        return dict(row) if row else None

    def last_seq(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(seq) FROM events").fetchone()
        return row[0] or 0

    def prune(self) -> None:
        """Удаляет события и снимки старше keep_days."""
        self._last_prune = time.time()
        cutoff = time.time() - self._keep_days * 86400
        with self._lock:
            old = [r[0] for r in self._db.execute("SELECT uid FROM events WHERE ts < ?", (cutoff,))]
            if old:
                self._db.executemany("DELETE FROM deliveries WHERE event_uid = ?", [(u,) for u in old])
                self._db.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
                self._db.commit()
        cutoff_day = datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d")
        if self._files.is_dir():
            for folder in self._files.iterdir():
                if folder.is_dir() and folder.name < cutoff_day:
                    shutil.rmtree(folder, ignore_errors=True)
        if old:
            log.info("Журнал событий: удалено %d записей старше %d дней", len(old), self._keep_days)
