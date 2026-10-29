import numpy as np

from track_seeds.analyzers import PresenceAnalyzer, PresenceTracker, Transition
from track_seeds.camera import Frame
from track_seeds.config import PresenceSettings
from track_seeds.detection import Detection
from track_seeds.events import PeopleCountChanged, PersonEntered, PersonLeft


def feed(tracker: PresenceTracker, seq: list[tuple[float, int]]) -> list[tuple[float, Transition]]:
    return [(ts, t) for ts, count in seq if (t := tracker.update(count, ts))]


def timeline(*segments: tuple[float, float, int], step: float = 0.5) -> list[tuple[float, int]]:
    """Отрезки (от, до, людей) → отсчёты детектора с шагом step."""
    return [(start + i * step, count) for start, end, count in segments for i in range(round((end - start) / step))]


def test_enter_requires_consecutive_hits():
    tracker = PresenceTracker(enter_hits=3, leave_after_s=10)
    assert feed(tracker, [(0, 1), (1, 1), (2, 0), (3, 1), (4, 1)]) == []
    assert tracker.update(1, 5) is Transition.ENTERED
    assert tracker.entered_at == 3


def test_short_gaps_do_not_trigger_leave():
    tracker = PresenceTracker(enter_hits=1, leave_after_s=5)
    events = feed(tracker, [(0, 1), (2, 0), (4, 0), (4.5, 1), (8, 0), (9, 1)])
    assert events == [(0, Transition.ENTERED)]
    assert tracker.present


def test_leave_after_timeout_and_reenter():
    tracker = PresenceTracker(enter_hits=1, leave_after_s=5)
    events = feed(tracker, [(0, 1), (3, 1), (6, 0), (8, 0), (20, 1)])
    assert events == [(0, Transition.ENTERED), (8, Transition.LEFT), (20, Transition.ENTERED)]


def test_enter_reports_max_count_seen_while_confirming():
    tracker = PresenceTracker(enter_hits=2, leave_after_s=10)
    feed(tracker, [(0, 1), (0.5, 2)])
    assert tracker.count == 2


def test_one_of_two_leaves_reports_count_change():
    tracker = PresenceTracker(enter_hits=2, leave_after_s=10, count_window_s=5, count_change_after_s=10)
    events = feed(tracker, timeline((0, 10, 2), (10, 40, 1)))

    assert [t for _, t in events] == [Transition.ENTERED, Transition.COUNT_CHANGED]
    changed_at = events[1][0]
    assert 20 <= changed_at <= 25
    assert (tracker.previous_count, tracker.count) == (2, 1)


def test_flickering_detection_does_not_spam():
    tracker = PresenceTracker(enter_hits=2, leave_after_s=10, count_window_s=5, count_change_after_s=10)
    flicker = [(i * 0.5, 2 if i % 2 else 1) for i in range(240)]  # 2 мин: 1,2,1,2…
    events = feed(tracker, flicker)
    assert [t for _, t in events] == [Transition.ENTERED]
    assert tracker.count == 2


def test_everyone_leaving_is_left_not_count_change():
    tracker = PresenceTracker(enter_hits=2, leave_after_s=10, count_window_s=5, count_change_after_s=10)
    events = feed(tracker, timeline((0, 10, 2), (10, 12, 1), (12, 40, 0)))
    assert [t for _, t in events] == [Transition.ENTERED, Transition.LEFT]
    assert tracker.previous_count == 2


class FakeDetector:
    def __init__(self, script: list[list[Detection]]):
        self._script = iter(script)

    def detect(self, image):
        return next(self._script)


def run_analyzer(script: list[list[Detection]], **settings) -> list:
    analyzer = PresenceAnalyzer(FakeDetector(script), PresenceSettings(_env_file=None, **settings), "Склад")
    image = np.zeros((100, 100, 3), np.uint8)
    return [e for i in range(len(script)) for e in analyzer.process(Frame(image=image, index=i, ts=float(i)))]


def test_analyzer_emits_events_and_filters_label_and_size():
    person = Detection("person", 0.9, (0, 0, 50, 50))
    tiny_person = Detection("person", 0.9, (0, 0, 2, 2))
    cat = Detection("cat", 0.9, (0, 0, 50, 50))
    events = run_analyzer([[person], [person, cat], [tiny_person, cat], [], []],
                          enter_hits=2, leave_after_s=2, min_box_area=0.01)

    assert [type(e) for e in events] == [PersonEntered, PersonLeft]
    entered, left = events
    assert entered.camera == "Склад" and entered.count == 1 and entered.ts == 1.0
    assert entered.snapshot and entered.snapshot[:2] == b"\xff\xd8"  # JPEG
    assert left.ts == 3.0 and left.duration_s == 1.0


def test_analyzer_count_change_event_can_be_disabled():
    a, b = Detection("person", 0.9, (0, 0, 30, 30)), Detection("person", 0.9, (50, 50, 30, 30))
    script = [[a, b]] * 5 + [[a]] * 10
    settings = dict(enter_hits=1, leave_after_s=10, count_window_s=2, count_change_after_s=3)

    events = run_analyzer(script, **settings)
    assert [type(e) for e in events] == [PersonEntered, PeopleCountChanged]
    assert (events[1].previous, events[1].count) == (2, 1)

    assert [type(e) for e in run_analyzer(script, **settings, count_changes=False)] == [PersonEntered]
