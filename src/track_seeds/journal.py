"""Журнал заездов машин: SQLite + снимки на диске."""

import sqlite3
from datetime import datetime
from pathlib import Path

from .bus import EventBus
from .events import VehicleArrived, VehicleLeft
from .plates.names import KIND_NAMES
from .plates.russian import to_cyrillic

_SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    vehicle_id       TEXT PRIMARY KEY,
    camera           TEXT NOT NULL,
    arrived_at       TEXT NOT NULL,
    left_at          TEXT,
    duration_s       REAL,
    plate            TEXT,
    plate_verified   INTEGER NOT NULL,
    plate_confidence REAL NOT NULL,
    color            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    snapshot         TEXT,
    plate_snapshot   TEXT
);
CREATE INDEX IF NOT EXISTS visits_arrived ON visits (arrived_at);
CREATE INDEX IF NOT EXISTS visits_plate ON visits (plate);
"""

COLUMNS = ("arrived_at", "left_at", "duration_s", "plate", "plate_verified", "color", "kind", "camera", "snapshot")


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class VehicleJournal:
    """Подписчик шины. Пишется только из потока шины, читается CLI отдельным соединением."""

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir
        self._snapshots = data_dir / "vehicles"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = connect(data_dir)

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(VehicleArrived, self.arrived)
        bus.subscribe(VehicleLeft, self.left)

    def arrived(self, event: VehicleArrived) -> None:
        stamp = datetime.fromtimestamp(event.ts)
        folder = self._snapshots / stamp.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{stamp:%H%M%S}_{(event.plate or 'no-plate').replace(' ', '')}_{event.vehicle_id[:6]}"
        snapshot = self._save(folder / f"{name}.jpg", event.snapshot)
        plate_snapshot = self._save(folder / f"{name}_plate.jpg", event.plate_image)
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO visits (vehicle_id, camera, arrived_at, plate, plate_verified, "
                "plate_confidence, color, kind, snapshot, plate_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event.vehicle_id, event.camera, _when(event.ts), event.plate, event.plate_verified,
                 event.plate_confidence, event.color, KIND_NAMES.get(event.kind, event.kind), snapshot, plate_snapshot),
            )

    def left(self, event: VehicleLeft) -> None:
        with self._db:
            self._db.execute("UPDATE visits SET left_at = ?, duration_s = ? WHERE vehicle_id = ?",
                             (_when(event.ts), round(event.duration_s, 1), event.vehicle_id))

    def _save(self, path: Path, data: bytes | None) -> str | None:
        if not data:
            return None
        path.write_bytes(data)
        return str(path.relative_to(self._dir))


def connect(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(data_dir / "vehicles.db", check_same_thread=False)
    db.executescript(_SCHEMA)
    return db


# Незакрытый заезд старше этого не продолжается: машина наверняка уезжала, пока
# программа не работала, и вернулась — это новый заезд.
_RESUME_WITHIN_S = 24 * 3600


def find_open_visit(data_dir: Path, camera: str, plate: str) -> tuple[str, float] | None:
    """Незакрытый заезд машины с этим номером: (vehicle_id, время заезда) или None."""
    if not (data_dir / "vehicles.db").is_file():
        return None
    db = connect(data_dir)
    try:
        row = db.execute("SELECT vehicle_id, arrived_at FROM visits WHERE camera = ? AND plate = ? AND left_at IS NULL "
                         "ORDER BY arrived_at DESC LIMIT 1", (camera, plate)).fetchone()
    finally:
        db.close()
    if row is None:
        return None
    arrived = datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S").timestamp()
    return (row[0], arrived) if datetime.now().timestamp() - arrived < _RESUME_WITHIN_S else None


def recent_visits(data_dir: Path, limit: int, plate: str | None = None) -> list[sqlite3.Row]:
    if not (data_dir / "vehicles.db").is_file():
        return []
    db = connect(data_dir)
    db.row_factory = sqlite3.Row
    query = f"SELECT {', '.join(COLUMNS)} FROM visits"
    params: list[object] = []
    if plate:
        query += " WHERE replace(plate, ' ', '') LIKE ?"
        params.append(f"%{to_cyrillic(plate.replace(' ', ''))}%")
    query += " ORDER BY arrived_at DESC LIMIT ?"
    params.append(limit)
    try:
        return db.execute(query, params).fetchall()
    finally:
        db.close()
