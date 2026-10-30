from pathlib import Path

import cv2
import numpy as np
import pytest

from track_seeds.analyzers.seeds import SeedsAnalyzer
from track_seeds.camera import Frame
from track_seeds.config import SeedSample, SeedsConfig, SeedZone
from track_seeds.detection import Detection
from track_seeds.events import SeedsLevelChanged
from track_seeds.notify.messages import format_event
from track_seeds.seeds import Reading, ZoneDebounce, ZoneModel, extract, extract_patch, is_grayscale, order_quad

W, H = 640, 360
BOTTOM = [(0.40, 0.70), (0.60, 0.70), (0.60, 0.85), (0.40, 0.85)]
MIDDLE = [(0.40, 0.40), (0.60, 0.40), (0.60, 0.55), (0.40, 0.55)]


def seeds_texture(h: int, w: int, seed: int = 0) -> np.ndarray:
    """Насыпь семечек: тёмные вытянутые зёрна со светлой полоской на коричневом фоне."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), (40, 60, 90), np.uint8)
    for _ in range(h * w // 40):
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        axes = (int(rng.integers(4, 7)), int(rng.integers(2, 4)))
        angle = float(rng.uniform(0, 180))
        cv2.ellipse(img, (cx, cy), axes, angle, 0, 360, (25, 25, 30), -1)
        cv2.ellipse(img, (cx, cy), (axes[0] - 1, 1), angle, 0, 360, (150, 150, 160), -1)
    return img


def metal_texture(h: int, w: int, seed: int = 0) -> np.ndarray:
    """Пустой бункер: гладкий серый металл с плавным бликом и редкими царапинами."""
    rng = np.random.default_rng(seed + 100)
    x = np.linspace(0, 1, w)[None, :]
    y = np.linspace(0, 1, h)[:, None]
    gray = 150 + 30 * np.sin(x * 2.5 + y) + rng.normal(0, 1.5, (h, w))
    img = np.repeat(np.clip(gray, 0, 255).astype(np.uint8)[..., None], 3, axis=2)
    for _ in range(6):
        p1 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        p2 = (p1[0] + int(rng.integers(-60, 60)), p1[1] + int(rng.integers(-10, 10)))
        cv2.line(img, p1, p2, (120, 120, 125), 1)
    return img


def scene(level: float, light: float = 1.0, ir: bool = False, seed: int = 0) -> np.ndarray:
    """Кадр: бункер в центре, снизу насыпаны семена до уровня level (0..1 высоты бункера)."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), 90, np.uint8)
    top, bottom, left, right = int(0.1 * H), int(0.9 * H), int(0.3 * W), int(0.7 * W)
    img[top:bottom, left:right] = metal_texture(bottom - top, right - left)
    surface = int(bottom - level * (bottom - top))
    if surface < bottom:
        img[surface:bottom, left:right] = seeds_texture(bottom - surface, right - left, seed=1)
    f = img.astype(np.float32) * light
    if ir:
        gray = cv2.cvtColor(np.clip(f, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
        f = np.repeat(gray[..., None], 3, 2)
    f += rng.normal(0, 2, f.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


# --- геометрия ---

def test_order_quad_is_clockwise_from_top_left():
    shuffled = [(0.6, 0.85), (0.4, 0.7), (0.4, 0.85), (0.6, 0.7)]
    assert order_quad(shuffled) == [(0.4, 0.7), (0.6, 0.7), (0.6, 0.85), (0.4, 0.85)]


def test_patch_size_does_not_depend_on_stream_resolution():
    small = extract_patch(scene(0.9), BOTTOM)
    big = extract_patch(cv2.resize(scene(0.9), (2560, 1440)), BOTTOM)
    assert small.shape == big.shape


def test_grayscale_frame_detected():
    assert not is_grayscale(scene(0.9))
    assert is_grayscale(scene(0.9, ir=True))


# --- признаки и эталоны ---

def features(image: np.ndarray, points=BOTTOM):
    return extract(extract_patch(image, points), is_grayscale(image))


def test_two_class_model_separates_seeds_and_metal_under_light_changes():
    model = ZoneModel([features(scene(0.9))], [features(scene(0.0))])
    for light in (0.6, 0.8, 1.0, 1.3):
        for seed in range(3):
            assert model.score(features(scene(0.9, light, seed=seed))).value > 0.6
            assert model.score(features(scene(0.0, light, seed=seed))).value < 0.4


def test_day_samples_work_for_night_ir_frames():
    model = ZoneModel([features(scene(0.9))], [features(scene(0.0))])
    assert model.score(features(scene(0.9, ir=True))).value > 0.6
    assert model.score(features(scene(0.0, ir=True))).value < 0.4


def test_novelty_flags_frame_unlike_any_sample():
    model = ZoneModel([features(scene(0.9))], [features(scene(0.0))])
    assert model.score(features(scene(0.9, seed=5))).novelty < 1
    # зону закрыли чем-то пёстрым — ни семена, ни пустой металл
    other = np.random.default_rng(0).integers(0, 255, (H, W, 3), dtype=np.uint8)
    assert model.score(features(other)).novelty > 1


def test_one_class_model_only_full_samples():
    model = ZoneModel([features(scene(0.9, seed=1)), features(scene(0.9, seed=2))], [])
    assert model.one_class
    assert model.score(features(scene(0.9, seed=3))).value > 0.5
    assert model.score(features(scene(0.0))).value < 0.5


# --- устойчивое состояние ---

def feed(debounce: ZoneDebounce, start: float, seconds: int, reading: Reading) -> list[float]:
    """Подаёт показания раз в секунду; возвращает моменты смены состояния."""
    changes = []
    for t in range(seconds):
        if debounce.update(start + t, reading):
            changes.append(start + t)
    return changes


def test_debounce_needs_full_window_of_agreement():
    d = ZoneDebounce(empty_after_s=30, refill_after_s=10, agreement=0.7)
    assert feed(d, 0, 10, Reading.COVERED) == []  # окно 10 с ещё не накоплено
    feed(d, 10, 5, Reading.COVERED)
    assert d.stable is Reading.COVERED
    assert feed(d, 15, 25, Reading.UNCOVERED) == []  # 25 с пусто — ещё рано
    changes = feed(d, 40, 10, Reading.UNCOVERED)
    assert len(changes) == 1 and 15 + 26 <= changes[0] <= 15 + 31  # ≈ через 30 с после того, как опустело
    assert d.stable is Reading.UNCOVERED


def test_debounce_tolerates_flicker_but_not_majority():
    d = ZoneDebounce(30, 10, 0.7)
    feed(d, 0, 20, Reading.COVERED)
    # каждое 4-е показание ошибочное «пусто» — 25% шума не должны дать тревогу
    for t in range(20, 200):
        d.update(t, Reading.UNCOVERED if t % 4 == 0 else Reading.COVERED)
    assert d.stable is Reading.COVERED


def test_debounce_ignores_unknown_and_waits_for_data():
    d = ZoneDebounce(30, 10, 0.7)
    feed(d, 0, 20, Reading.COVERED)
    feed(d, 20, 120, Reading.UNKNOWN)  # человек загородил на 2 минуты
    assert d.stable is Reading.COVERED
    assert d.blind_for(139) == pytest.approx(120)
    # человек ушёл, а там пусто: решение только когда пустых кадров достаточно
    changes = feed(d, 140, 40, Reading.UNCOVERED)
    assert changes and changes[0] >= 155


# --- анализатор целиком ---

class FakePeople:
    def __init__(self) -> None:
        self.boxes: list[tuple[int, int, int, int]] = []

    def detect(self, image):
        return [Detection("person", 0.9, b) for b in self.boxes]


def make_analyzer(tmp_path: Path, people=None, **kw) -> SeedsAnalyzer:
    (tmp_path / "s").mkdir(exist_ok=True)
    cv2.imwrite(str(tmp_path / "s/full.jpg"), scene(0.95), [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(tmp_path / "s/empty.jpg"), scene(0.0), [cv2.IMWRITE_JPEG_QUALITY, 95])
    zones = [SeedZone(id="b", name="Дно", kind="empty", points=BOTTOM),
             SeedZone(id="m", name="Середина", kind="low", points=MIDDLE)]
    samples = [SeedSample(id="1", image="s/full.jpg", ts=0, labels={"b": "full", "m": "full"}),
               SeedSample(id="2", image="s/empty.jpg", ts=0, labels={"b": "empty", "m": "empty"})]
    kw.setdefault("ignore_people", people is not None)
    settings = SeedsConfig(zones=zones, samples=samples, empty_after_s=30, refill_after_s=10, **kw)
    return SeedsAnalyzer(settings, "Дробилка", tmp_path, people)


def run(analyzer: SeedsAnalyzer, start: int, seconds: int, level: float, **scene_kw) -> list[SeedsLevelChanged]:
    events = []
    image = scene(level, **scene_kw)
    for t in range(start, start + seconds):
        events += analyzer.process(Frame(image=image, index=t, ts=1_000_000 + t))
    return events


def test_full_cycle_low_empty_reminder_refill(tmp_path):
    a = make_analyzer(tmp_path, remind_every_s=60)
    start = run(a, 0, 15, 0.95)
    assert [(e.level, e.quiet) for e in start] == [("ok", True)]  # первое «есть» — только в журнал

    low = run(a, 15, 40, 0.3)  # середина открылась
    assert [e.level for e in low] == ["low"]
    assert low[0].zones == ("Середина",) and not low[0].quiet

    empty = run(a, 55, 100, 0.0)
    assert [(e.level, e.reminder) for e in empty] == [("empty", 0), ("empty", 1)]
    assert empty[0].snapshot and empty[0].frame

    refill = run(a, 155, 20, 0.95)
    assert [(e.level, e.previous) for e in refill] == [("ok", "empty")]
    n = format_event(refill[0])
    assert n.title == "Дробилка: семена досыпали" and "Было пусто" in n.message


def test_person_in_front_of_zone_does_not_trigger(tmp_path):
    people = FakePeople()
    a = make_analyzer(tmp_path, people)
    run(a, 0, 15, 0.95)
    people.boxes = [(int(0.35 * W), int(0.3 * H), int(0.3 * W), int(0.65 * H))]
    # человек загородил зону, за ним «пусто» по картинке — тревоги нет
    assert run(a, 15, 120, 0.0) == []
    assert a.status()["zones"][0]["reason"] == "people"


def test_darkness_is_unknown_and_reported_after_timeout(tmp_path):
    a = make_analyzer(tmp_path, unknown_alert_s=60)
    run(a, 0, 15, 0.95)
    events = run(a, 15, 70, 0.95, light=0.05)
    assert [e.level for e in events] == ["unknown"]
    assert events[0].last_known == "ok"
    assert "не видно желоб" in format_event(events[0]).title
    back = run(a, 85, 15, 0.95)
    assert [(e.level, e.previous) for e in back] == [("ok", "unknown")]


def test_new_settings_keep_state_without_repeat_alarm(tmp_path):
    a = make_analyzer(tmp_path)
    run(a, 0, 15, 0.95)
    assert [e.level for e in run(a, 15, 40, 0.0)] == ["empty"]
    b = make_analyzer(tmp_path, remind_every_s=0)
    b.inherit(a)
    assert run(b, 55, 30, 0.0) == []
    assert b.level == "empty"


def test_zone_without_samples_is_ignored(tmp_path):
    a = make_analyzer(tmp_path)
    a._settings.zones.append(SeedZone(id="x", name="Новая", points=MIDDLE))
    b = SeedsAnalyzer(a._settings, "Дробилка", tmp_path)
    assert [e.level for e in run(b, 0, 15, 0.95)] == ["ok"]
    assert b.status()["zones"][2]["reason"] == "no_samples"


def test_one_class_samples_taken_back_to_back_do_not_make_it_hypersensitive():
    """Два почти одинаковых эталона «есть» (сняли подряд) не должны сужать допуск до нуля."""
    single = ZoneModel([features(scene(0.9, seed=1))], [])
    twins = ZoneModel([features(scene(0.9, seed=1)), features(scene(0.9, seed=1))], [])
    assert twins.radius >= single.radius
    assert twins.score(features(scene(0.9, light=0.8, seed=4))).value > 0.5  # свет чуть другой — семена есть


# --- привод: работает / стоит ---

DRIVE = [(0.76, 0.60), (0.96, 0.60), (0.96, 0.90), (0.76, 0.90)]
_CHAIN = seeds_texture(160, 400, seed=7)  # «цепь»: мелкий рисунок, который сдвигается, пока привод крутится


def with_drive(image: np.ndarray, t: int, running: bool) -> np.ndarray:
    image = image.copy()
    x0, y0, x1, y1 = int(0.76 * W), int(0.60 * H), int(0.96 * W), int(0.90 * H)
    shift = (t * 37) % 200 if running else 0
    image[y0:y1, x0:x1] = _CHAIN[:y1 - y0, shift:shift + x1 - x0]
    return image


def make_drive_analyzer(tmp_path: Path, **kw) -> SeedsAnalyzer:
    a = make_analyzer(tmp_path, **kw)
    a._settings.zones.append(SeedZone(id="d", name="Привод", kind="motion", points=DRIVE))
    return SeedsAnalyzer(a._settings, "Дробилка", tmp_path)


def run_drive(analyzer: SeedsAnalyzer, start: int, seconds: int, level: float, running: bool,
              **scene_kw) -> list[SeedsLevelChanged]:
    events = []
    image = scene(level, **scene_kw)
    for t in range(start, start + seconds):
        events += analyzer.process(Frame(image=with_drive(image, t, running), index=t, ts=1_000_000 + t))
    return events


def test_changed_share_sees_motion_but_not_light():
    from track_seeds.seeds import changed_share, motion_patch
    still = motion_patch(with_drive(scene(0.9), 0, False), DRIVE)
    darker = motion_patch((with_drive(scene(0.9), 0, False) * 0.7).astype(np.uint8), DRIVE)
    moved = motion_patch(with_drive(scene(0.9), 1, True), DRIVE)
    assert changed_share(darker, still) < 0.01  # экспозиция сменилась — не движение
    assert changed_share(moved, still) > 0.2


def test_stopped_crusher_does_not_alarm_and_restart_without_seeds_does(tmp_path):
    a = make_drive_analyzer(tmp_path, stopped_after_s=20, running_after_s=5, remind_every_s=60)
    assert [e.level for e in run_drive(a, 0, 15, 0.95, True)] == ["ok"]
    assert a.running is True

    stop = run_drive(a, 15, 30, 0.95, False)
    assert [(e.level, e.quiet) for e in stop] == [("stopped", False)]
    assert a.running is False and "остановилась" in format_event(stop[0]).title
    assert format_event(stop[0]).priority == "high" and "семена есть" in format_event(stop[0]).message

    # стоит, а бункер опустел (или лопасть замерла перед зоной) — молчим, напоминаний тоже нет
    assert run_drive(a, 45, 200, 0.0, False) == []

    # запустили без семян — тревога, но не раньше, чем пусто подтвердится уже в работе
    start = run_drive(a, 245, 60, 0.0, True)
    assert [(e.level, e.previous, e.quiet) for e in start] == [("empty", "stopped", False)]
    assert start[0].ts - (1_000_000 + 245) >= 30


def test_restart_with_seeds_notifies_and_reminders_stop_when_stopped(tmp_path):
    a = make_drive_analyzer(tmp_path, remind_every_s=60)
    run_drive(a, 0, 15, 0.95, True)
    assert [e.level for e in run_drive(a, 15, 40, 0.0, True)] == ["empty"]  # кончились в работе — тревога
    stop = run_drive(a, 55, 200, 0.0, False)  # остановили: «стоит», и больше не напоминаем
    assert [e.level for e in stop] == ["stopped"]
    back = run_drive(a, 255, 30, 0.95, True)  # досыпали и запустили
    assert [(e.level, e.previous, e.quiet) for e in back] == [("ok", "stopped", False)]
    assert "снова работает" in format_event(back[0]).title


def test_stop_and_start_are_quiet_when_disabled(tmp_path):
    a = make_drive_analyzer(tmp_path, notify_stopped=False)
    run_drive(a, 0, 15, 0.95, True)
    assert [(e.level, e.quiet) for e in run_drive(a, 15, 40, 0.95, False)] == [("stopped", True)]
    assert [(e.level, e.quiet) for e in run_drive(a, 55, 30, 0.95, True)] == [("ok", True)]


def test_crusher_already_stopped_at_server_start_is_quiet(tmp_path):
    a = make_drive_analyzer(tmp_path)
    assert [(e.level, e.previous, e.quiet) for e in run_drive(a, 0, 40, 0.95, False)] == [("stopped", None, True)]


def test_drive_state_unknown_at_start_holds_decision(tmp_path):
    """Пока не ясно, крутится ли привод (человек закрыл зону), о семенах не решаем."""
    people = FakePeople()
    a = make_drive_analyzer(tmp_path)
    a._detector = people
    people.boxes = [(int(0.74 * W), int(0.55 * H), int(0.24 * W), int(0.4 * H))]
    assert run_drive(a, 0, 60, 0.0, False) == []
    assert a.running is None and a.status()["zones"][2]["reason"] == "people"


def test_drive_zone_needs_no_samples_and_is_excluded_from_check(tmp_path):
    a = make_drive_analyzer(tmp_path)
    assert [z["name"] for z in a.evaluate(scene(0.95))] == ["Дно", "Середина"]
    drives = a.evaluate_drives(with_drive(scene(0.95), 0, True), with_drive(scene(0.95), 1, True))
    assert drives[0]["moving"] and drives[0]["motion"] > 0.2
    assert not a.evaluate_drives(with_drive(scene(0.95), 0, False), with_drive(scene(0.95), 1, False))[0]["moving"]


def test_short_empty_delay_does_not_fire_while_stop_is_being_confirmed(tmp_path):
    """«Закончились» быстрее, чем подтверждается «стоит»: дробилку остановили, лопасть замерла перед зоной
    (желоб выглядит пустым) — тревоги нет, потому что кадры с неподвижным приводом не в счёт."""
    a = make_drive_analyzer(tmp_path, stopped_after_s=20, running_after_s=5)
    a._settings.empty_after_s = 10
    b = SeedsAnalyzer(a._settings, "Дробилка", tmp_path)
    run_drive(b, 0, 15, 0.95, True)
    assert [e.level for e in run_drive(b, 15, 60, 0.0, False)] == ["stopped"]
    # а в работе пустой желоб — тревога через те же 10 с
    start = run_drive(b, 75, 30, 0.0, True)
    assert [e.level for e in start] == ["empty"] and start[0].ts - (1_000_000 + 75) <= 5 + 10 + 2


def test_default_delays_are_short(tmp_path):
    s = SeedsConfig()
    assert (s.interval_s, s.empty_after_s, s.refill_after_s, s.stopped_after_s) == (0.5, 5, 5, 10)
    assert s.ignore_people  # рука рабочего в желобе — не «закончились»


class CountingPeople(FakePeople):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return super().detect(image)


def test_people_detector_runs_only_when_a_frame_could_change_something(tmp_path):
    """Детектор людей дорогой: семена есть и привод крутится — не нужен; желоб «пусто» — раз в секунду, не чаще."""
    people = CountingPeople()
    a = make_drive_analyzer(tmp_path)
    a._detector = people
    run_drive(a, 0, 15, 0.95, True)  # привод и семена определяются — пока состояния нет, детектор работает
    people.calls = 0
    run_drive(a, 15, 30, 0.95, True)
    assert people.calls == 0
    image = scene(0.0)
    for i in range(20):  # 10 с пустого желоба, проверка дважды в секунду
        a.process(Frame(image=with_drive(image, 100 + i, True), index=100 + i, ts=1_000_045 + i * 0.5))
    assert 8 <= people.calls <= 11
