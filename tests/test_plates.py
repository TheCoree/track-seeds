import sqlite3
import time

import numpy as np
import pytest

from track_seeds.analyzers.vehicles import VehicleAnalyzer, find_vehicles
from track_seeds.camera import Frame
from track_seeds.config import PlatesSettings
from track_seeds.detection import Detection
from track_seeds.events import VehicleArrived, VehicleLeft
from track_seeds.journal import VehicleJournal, find_open_visit, recent_visits
from track_seeds.plates import PlateRead, PlateReader, VehicleTracker, estimate_color, parse_ru, vote
from track_seeds.plates.russian import to_cyrillic


@pytest.mark.parametrize(("raw", "display", "corrections"), [
    ("C484HX70", "С484НХ 70", 0),
    ("c484hx 777", "С484НХ 777", 0),
    ("K6940B70", "К694ОВ 70", 1),  # 0 на месте буквы О
    ("AB12377", "АВ123 77", 0),  # такси
    ("O087470", "О0874 70", 0),  # полиция
    ("AB123477", "АВ1234 77", 0),  # прицеп
])
def test_parse_ru_valid(raw, display, corrections):
    plate = parse_ru(raw)
    assert plate is not None and plate.display == display and plate.corrections == corrections


@pytest.mark.parametrize("raw", ["C484HX7", "416AEP0", "K4823", "", "ZZZ99999", "A123BC00", "A123BC077",
                                 "A123BC477"])
def test_parse_ru_rejects(raw):
    assert parse_ru(raw) is None


def test_to_cyrillic_for_search():
    assert to_cyrillic("c484hx") == "С484НХ"


def read(text: str, conf: float = 0.9, corrections: int = 0) -> PlateRead:
    return PlateRead(text, text, conf, corrections, (0, 0, 10, 5), np.zeros((5, 10, 3), np.uint8))


def test_vote_prefers_repeated():
    best, count = vote([read("C484HX70"), read("C484HX70", 0.8), read("C484HK70", 0.95)])
    assert best.text == "C484HX70" and count == 2


def test_vote_penalizes_corrections_and_handles_empty():
    best, _ = vote([read("C7290870", 0.9, corrections=1), read("C729OB70", 0.85)])
    assert best.text == "C729OB70"
    assert vote([]) == (None, 0)


@pytest.mark.parametrize(("bgr", "name"), [
    ((240, 240, 240), "белый"), ((180, 180, 180), "серебристый"), ((90, 90, 90), "серый"), ((20, 20, 20), "чёрный"),
    ((30, 30, 200), "красный"), ((180, 60, 20), "синий"), ((40, 160, 40), "зелёный"), ((40, 70, 110), "коричневый"),
])
def test_estimate_color_solid(bgr, name):
    image = np.full((100, 200, 3), bgr, np.uint8)
    assert estimate_color(image, (0, 0, 200, 100)) == name


def car(x: int, y: int = 100, w: int = 200, h: int = 120, label: str = "car") -> Detection:
    return Detection(label, 0.9, (x, y, w, h))


def test_tracker_stop_occlusion_and_leave():
    tracker = VehicleTracker(stop_after_s=3, stop_tolerance=0.05, leave_after_s=10)
    for ts, x in [(0, 500), (1, 400), (2, 300), (3, 300), (4, 301), (5, 302)]:
        stopped, gone = tracker.update([car(x)], ts)
        assert not gone
        if ts < 5:
            assert not stopped
    assert stopped == tracker.tracks and len(tracker.tracks) == 1
    track = tracker.tracks[0]

    # 8 секунд машину закрывал человек — та же машина, не новая
    tracker.update([], 9)
    tracker.update([car(302)], 13)
    assert tracker.tracks == [track]
    _, gone = tracker.update([], 23)
    assert gone == [track] and tracker.tracks == []


def test_tracker_moving_car_never_stops():
    tracker = VehicleTracker(stop_after_s=3, stop_tolerance=0.05, leave_after_s=10)
    assert not any(tracker.update([car(1000 - ts * 50)], ts)[0] for ts in range(15))


def test_find_vehicles_merges_car_and_truck_boxes_of_one_vehicle():
    settings = PlatesSettings(_env_file=None, min_vehicle_area=0.01)
    dets = [car(100, w=400, h=300, label="truck"), car(250, w=240, h=280), Detection("person", 0.9, (0, 0, 50, 100)),
            car(900, w=20, h=10)]  # слишком маленькая
    assert [d.label for d in find_vehicles(dets, settings, (720, 1280, 3))] == ["truck"]


class ScriptedDetector:
    def __init__(self, timeline):
        self._timeline = timeline

    def detect(self, image):
        return self._timeline(int(image[0, 0, 0]))


class FakeReader:
    def __init__(self, texts):
        self._texts = iter(texts)

    def read(self, image, region=None):
        text = next(self._texts, None)
        return parse_and_read(text) if text else None


def parse_and_read(text: str) -> PlateRead | None:
    """Как настоящий PlateReader: не похожее на российский номер не возвращается."""
    parsed = parse_ru(text)
    if parsed is None:
        return None
    return PlateRead(parsed.text, parsed.display, 0.95, parsed.corrections, (10, 10, 50, 12),
                     np.zeros((12, 50, 3), np.uint8))


def run_vehicles(seconds: int, car_at, reads, open_visit=None, **settings) -> list:
    """Тик раз в секунду; car_at(t) — x машины или None; в пикселе [0,0] кадра — номер секунды."""
    plates = PlatesSettings(_env_file=None, stop_after_s=3, leave_after_s=5, read_timeout_s=6, confirm_reads=2,
                            min_vehicle_area=0, **settings)
    detector = ScriptedDetector(lambda t: [] if car_at(t) is None else [car(car_at(t))])
    analyzer = VehicleAnalyzer(detector, FakeReader(reads), plates, "Ворота", open_visit=open_visit)
    events = []
    for t in range(seconds):
        image = np.full((480, 1280, 3), 128, np.uint8)
        image[0, 0, 0] = t
        events += analyzer.process(Frame(image=image, index=t, ts=100.0 + t))
    return events


def test_vehicle_arrives_reads_plate_and_leaves():
    # подъезжает 0–2 с, стоит 2–14 с, уезжает
    position = lambda t: 900 - t * 200 if t < 2 else 500 if t <= 14 else None
    events = run_vehicles(25, position, ["C484HX7", "C484HX70", "C484HX70"])

    arrived, left = events
    assert isinstance(arrived, VehicleArrived) and isinstance(left, VehicleLeft)
    assert arrived.plate == "С484НХ 70" and arrived.plate_verified and arrived.color == "серый"
    assert arrived.ts == 105.0  # остановилась в 102, неподвижна 3 с
    assert arrived.snapshot[:2] == b"\xff\xd8" and arrived.plate_image[:2] == b"\xff\xd8"
    assert left.vehicle_id == arrived.vehicle_id and left.plate == "С484НХ 70"
    assert left.duration_s == 9.0  # 105 → 114 (последний раз видели)


def test_unreadable_car_is_silent_by_default():
    # номер не читается (далеко, мелкий) — ни «подъехала», ни «уехала»
    assert run_vehicles(25, lambda t: 500 if t < 12 else None, ["GX8697", "548WW7"]) == []


def test_unreadable_plate_reported_when_enabled():
    events = run_vehicles(15, lambda t: 500, [], notify_unreadable=True)
    (arrived,) = events
    assert arrived.plate is None and not arrived.plate_verified
    assert arrived.ts == 103.0


def test_car_outside_roi_is_ignored():
    # машина в левой части кадра (центр x≈0.47), зона — правая половина
    assert run_vehicles(15, lambda t: 500, ["A123BC77"] * 15, roi=(0.5, 0, 0.5, 1)) == []
    assert run_vehicles(15, lambda t: 500, ["A123BC77"] * 15, roi=(0.3, 0, 0.7, 1)) != []


def test_open_visit_from_journal_is_resumed_without_new_arrival():
    lookups = []

    def open_visit(camera, plate):
        lookups.append((camera, plate))
        return "old-visit", 50.0

    events = run_vehicles(25, lambda t: 500 if t <= 12 else None, ["A123BC77"] * 12, open_visit=open_visit)
    (left,) = events
    assert lookups == [("Ворота", "А123ВС 77")]
    assert isinstance(left, VehicleLeft) and left.vehicle_id == "old-visit" and left.duration_s == 112 - 50


def test_single_read_is_reported_as_unverified_on_timeout():
    (arrived,) = run_vehicles(15, lambda t: 500, ["A123BC77"])
    assert arrived.plate == "А123ВС 77" and not arrived.plate_verified


def test_short_stop_then_drive_on_reports_what_was_read():
    # стоит 0–3 с (одно прочтение на 3-й секунде), потом медленно трогается и остаётся в кадре
    events = run_vehicles(8, lambda t: 500 if t <= 3 else 500 - (t - 3) * 40, ["A123BC77"])
    assert [type(e) for e in events] == [VehicleArrived]
    assert events[0].plate == "А123ВС 77" and events[0].ts == 103.0


def test_short_stop_then_fast_departure_reports_arrival_and_departure():
    # стоит 0–3 с, затем резко уезжает — трекер теряет машину
    events = run_vehicles(12, lambda t: 500 if t <= 3 else None, ["A123BC77"])
    assert [type(e) for e in events] == [VehicleArrived, VehicleLeft]
    assert events[0].plate == "А123ВС 77" and events[1].duration_s == 0.0


def test_passing_car_is_ignored():
    assert run_vehicles(12, lambda t: 1200 - t * 100, ["A123BC77"] * 12) == []


def test_journal_writes_and_searches(tmp_path):
    journal = VehicleJournal(tmp_path)
    journal.arrived(VehicleArrived(camera="Ворота", ts=1_800_000_000, vehicle_id="abc", plate="С484НХ 70",
                                   plate_verified=True, plate_confidence=0.99, color="белый", kind="car",
                                   snapshot=b"\xff\xd8snap", plate_image=b"\xff\xd8plate"))
    journal.arrived(VehicleArrived(camera="Ворота", ts=1_800_000_100, vehicle_id="def", plate=None,
                                   plate_verified=False, plate_confidence=0, color="синий", kind="truck"))
    journal.left(VehicleLeft(camera="Ворота", ts=1_800_000_600, vehicle_id="abc", plate="С484НХ 70", color="белый",
                             kind="car", duration_s=600))

    rows = recent_visits(tmp_path, limit=10)
    assert [r["plate"] for r in rows] == [None, "С484НХ 70"]
    assert rows[0]["kind"] == "грузовая" and rows[0]["left_at"] is None
    assert rows[1]["duration_s"] == 600 and (tmp_path / rows[1]["snapshot"]).read_bytes() == b"\xff\xd8snap"

    assert [r["plate"] for r in recent_visits(tmp_path, 10, plate="c484hx")] == ["С484НХ 70"]  # латиницей
    assert recent_visits(tmp_path, 10, plate="999") == []
    assert sqlite3.connect(tmp_path / "vehicles.db").execute("SELECT count(*) FROM visits").fetchone() == (2,)


def test_recent_visits_without_journal(tmp_path):
    assert recent_visits(tmp_path, 10) == []
    assert find_open_visit(tmp_path, "Ворота", "А123ВС 77") is None


def test_find_open_visit(tmp_path):
    journal = VehicleJournal(tmp_path)
    now = time.time()
    for vid, ts, plate in [("old", now - 30 * 3600, "А123ВС 77"), ("new", now - 600, "А123ВС 77"),
                           ("gone", now - 300, "В456ОР 99")]:
        journal.arrived(VehicleArrived(camera="Ворота", ts=ts, vehicle_id=vid, plate=plate, plate_verified=True,
                                       plate_confidence=1, color="белый", kind="car"))
    journal.left(VehicleLeft(camera="Ворота", ts=now, vehicle_id="gone", plate="В456ОР 99", color="белый",
                             kind="car", duration_s=300))

    vid, arrived = find_open_visit(tmp_path, "Ворота", "А123ВС 77")
    assert vid == "new" and abs(arrived - (now - 600)) < 1
    assert find_open_visit(tmp_path, "Ворота", "В456ОР 99") is None  # уже уехала
    assert find_open_visit(tmp_path, "Склад", "А123ВС 77") is None  # другая камера


def test_find_open_visit_ignores_stale(tmp_path):
    VehicleJournal(tmp_path).arrived(VehicleArrived(camera="Ворота", ts=time.time() - 30 * 3600, vehicle_id="x",
                                                    plate="А123ВС 77", plate_verified=True, plate_confidence=1,
                                                    color="белый", kind="car"))
    assert find_open_visit(tmp_path, "Ворота", "А123ВС 77") is None


class _Box:
    def __init__(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


class _Plate:
    def __init__(self, width, confidence=0.9):
        self.bounding_box, self.confidence = _Box(10, 10, 10 + width, 10 + width // 4), confidence


class _Prediction:
    def __init__(self, plate, probs):
        self.plate, self.char_probs = plate, np.array(probs + [0.0] * (10 - len(probs)))


def stub_reader(plate_width: int, text: str, probs: list[float], fmt: str = "ru") -> PlateReader:
    """PlateReader без ONNX: подменённые детектор номеров и OCR, настоящая логика фильтров."""
    reader = object.__new__(PlateReader)
    reader._detector = type("D", (), {"predict": lambda self, img: [_Plate(plate_width)]})()
    reader._ocr = type("O", (), {"run": lambda self, img, return_confidence: [_Prediction(text, probs)]})()
    reader._grayscale, reader._format = False, fmt
    reader._min_width, reader._min_char_confidence = 80, 0.7
    return reader


IMAGE = np.zeros((300, 400, 3), np.uint8)


def test_reader_accepts_good_read():
    r = stub_reader(150, "M177MX82", [0.99] * 8).read(IMAGE)
    assert r.display == "М177МХ 82" and r.box == (10, 10, 150, 37)


@pytest.mark.parametrize(("width", "text", "probs", "why"), [
    (58, "C085EM12", [0.95] * 8, "номер мельче 80 px"),
    (150, "C085EM12", [0.95] * 7 + [0.54], "один символ неуверенный (как мусор с парковки)"),
    (150, "W613VMM", [0.99] * 7, "латиница, которой нет в российских номерах"),
    (150, "", [], "пусто"),
])
def test_reader_rejects(width, text, probs, why):
    assert stub_reader(width, text, probs).read(IMAGE) is None, why


def test_reader_any_format_keeps_raw_text():
    assert stub_reader(150, "W613VMM", [0.99] * 7, fmt="any").read(IMAGE).display == "W613VMM"
