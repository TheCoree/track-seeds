import threading
import time

import numpy as np

from track_seeds.bus import EventBus
from track_seeds.camera import Frame
from track_seeds.config import NotifySettings, load_settings
from track_seeds.events import CameraOffline, CameraOnline, Event
from track_seeds.pipeline import Pipeline


class FakeSource:
    def __init__(self, frames: int, connected: bool = True):
        self.connected = connected
        self.status_since = time.time()
        self._frames = frames
        self._index = -1

    def start(self):
        pass

    def stop(self):
        pass

    def wait_frame(self, after_index, timeout):
        if self._index + 1 >= self._frames:
            time.sleep(0.01)
            return None
        self._index += 1
        return Frame(image=np.zeros((4, 4, 3), np.uint8), index=self._index, ts=time.time())


class CountingAnalyzer:
    name = "counter"

    def __init__(self, interval_s: float):
        self.interval_s = interval_s
        self.calls = 0

    def process(self, frame):
        self.calls += 1
        return [Event(camera="c")]

    def annotate(self, image):
        pass


class Recorder:
    def __init__(self, bus: EventBus):
        self.events: list[Event] = []
        bus.subscribe(Event, self.events.append)


def run_for(pipeline: Pipeline, seconds: float) -> None:
    stop = threading.Event()
    threading.Timer(seconds, stop.set).start()
    pipeline.run(stop)


def test_analyzer_interval_and_publish():
    bus = EventBus()
    recorder = Recorder(bus)
    analyzer = CountingAnalyzer(interval_s=3600)
    bus.start()
    run_for(Pipeline(FakeSource(frames=50), [analyzer], bus, "c"), 0.2)
    bus.stop()
    assert analyzer.calls == 1  # остальные кадры пропущены по интервалу
    assert len(recorder.events) == 1


def test_camera_offline_then_online():
    bus = EventBus()
    recorder = Recorder(bus)
    source = FakeSource(frames=0, connected=False)
    source.status_since = time.time() - 100
    pipeline = Pipeline(source, [], bus, "Склад", offline_alert_s=60)
    bus.start()
    run_for(pipeline, 0.1)
    source.connected, source.status_since = True, time.time()
    run_for(pipeline, 0.1)
    bus.stop()

    offline, online = recorder.events
    assert isinstance(offline, CameraOffline) and isinstance(online, CameraOnline)
    assert 99 < online.downtime_s < 102


def test_settings_parse_channels(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NOTIFY_CHANNELS=ntfy, LOG\nCAMERA_NAME=Элеватор\nPRESENCE_LEAVE_AFTER_S=30\n", encoding="utf-8")
    settings = load_settings(env)
    assert settings.notify.channels == ["ntfy", "log"]
    assert settings.camera.name == "Элеватор"
    assert settings.presence.leave_after_s == 30
    assert NotifySettings(_env_file=None).channels == ["log"]
