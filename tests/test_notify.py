import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from track_seeds.bus import EventBus
from track_seeds.config import NotifySettings, NtfySettings
from track_seeds.events import CameraOffline, PeopleCountChanged, PersonEntered, PersonLeft
from track_seeds.notify import Notification, NotificationService, NtfyNotifier
from track_seeds.notify.messages import format_duration, format_event


@dataclass
class Captured:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeNtfy:
    url: str
    requests: list[Captured] = field(default_factory=list)
    reject_attachments: bool = False


@pytest.fixture
def ntfy() -> Iterator[FakeNtfy]:
    state = FakeNtfy(url="")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _handle(self):
            parts = urlsplit(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            state.requests.append(Captured(self.command, parts.path, query, dict(self.headers), body))
            status = 400 if state.reject_attachments and "filename" in query else 200
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_PUT = _handle

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()


def make_ntfy(url: str, **kw) -> NtfyNotifier:
    return NtfyNotifier(NtfySettings(_env_file=None, url=url, topic="seeds", **kw))


def test_text_message_utf8(ntfy):
    make_ntfy(ntfy.url, token="tk_secret").send(
        Notification(title="Склад: человек", message="Привет", tags=("warning",), priority="high"))

    (req,) = ntfy.requests
    assert (req.method, req.path) == ("POST", "/seeds")
    assert req.query == {"title": "Склад: человек", "priority": "high", "tags": "warning"}
    assert req.body.decode() == "Привет"
    assert req.headers["Authorization"] == "Bearer tk_secret"


def test_attachment_and_basic_auth(ntfy):
    make_ntfy(ntfy.url, username="u", password="p").send(
        Notification(title="T", message="Сообщение", image=b"\xff\xd8jpeg"))

    (req,) = ntfy.requests
    assert req.method == "PUT"
    assert req.query["message"] == "Сообщение" and req.query["filename"] == "snapshot.jpg"
    assert req.body == b"\xff\xd8jpeg"
    assert req.headers["Authorization"] == "Basic dTpw"


def test_attachment_rejected_falls_back_to_text(ntfy):
    ntfy.reject_attachments = True
    make_ntfy(ntfy.url).send(Notification(title="T", message="M", image=b"jpeg"))

    assert [r.method for r in ntfy.requests] == ["PUT", "POST"]
    assert ntfy.requests[1].body == b"M"


def test_missing_topic_is_config_error():
    with pytest.raises(ValueError, match="NTFY_TOPIC"):
        NtfyNotifier(NtfySettings(_env_file=None, topic=""))


class FlakyNotifier:
    name = "flaky"

    def __init__(self, failures: int):
        self.failures = failures
        self.sent: list[Notification] = []

    def send(self, notification):
        if self.failures:
            self.failures -= 1
            raise ConnectionError("boom")
        self.sent.append(notification)


def test_service_retries_and_strips_snapshot_via_bus():
    notifier = FlakyNotifier(failures=2)
    settings = NotifySettings(_env_file=None, retries=3, retry_delay_s=0, attach_snapshot=False)
    bus = EventBus()
    NotificationService([notifier], settings).attach(bus)
    bus.start()
    bus.publish(PersonEntered(camera="Склад", count=2, snapshot=b"jpeg"))
    bus.stop()

    (sent,) = notifier.sent
    assert sent.title == "Склад: человек в кадре" and sent.image is None


def test_messages():
    assert format_duration(42) == "42 с"
    assert format_duration(125) == "2 мин 5 с"
    assert format_duration(3 * 3600 + 61) == "3 ч 1 мин"
    assert "Был в кадре 1 мин 30 с" in format_event(PersonLeft(camera="К", duration_s=90)).message
    assert format_event(CameraOffline(camera="К", since=0)).priority == "high"
    left_one = format_event(PeopleCountChanged(camera="К", count=1, previous=2))
    assert left_one.title == "К: кто-то ушёл" and "людей в кадре: 1 (было 2)" in left_one.message
