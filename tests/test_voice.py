import re
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from hashlib import md5

import numpy as np
import pytest
from pydantic import SecretStr

from track_seeds.config import SeedsConfig, SpeakerConfig, VoiceConfig
from track_seeds.events import SeedsLevelChanged
from track_seeds.voice import Announcer, HikvisionSpeaker, SpeakerError, compose, in_hours, phrase_for
from track_seeds.voice.audio import alaw_encode, read_wav, resample, ulaw_encode, wav_bytes


# --- G.711: сверка с построчной реализацией из g711.c ---

def ulaw_reference(sample: int) -> int:
    exp_lut = [0] * 2 + [1] * 2 + [2] * 4 + [3] * 8 + [4] * 16 + [5] * 32 + [6] * 64 + [7] * 128
    sign = 0x80 if sample < 0 else 0
    if sign:
        sample = -sample
    sample = min(sample, 32635) + 0x84
    exponent = exp_lut[(sample >> 7) & 0xFF]
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def alaw_reference(pcm: int) -> int:
    seg_end = [0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF]
    pcm >>= 3
    if pcm >= 0:
        mask = 0xD5
    else:
        mask = 0x55
        pcm = -pcm - 1
    seg = next((i for i, end in enumerate(seg_end) if pcm <= end), 8)
    if seg >= 8:
        return 0x7F ^ mask
    value = seg << 4 | ((pcm >> 1) if seg < 2 else (pcm >> seg)) & 0x0F
    return value ^ mask


def test_g711_matches_reference_for_all_16bit_values():
    pcm = np.arange(-32768, 32768, 7, dtype=np.int32)
    samples = (pcm / 32767).astype(np.float32)
    quantized = (np.clip(samples, -1, 1) * 32767).astype(np.int32)  # как to_pcm16
    assert list(ulaw_encode(samples)) == [ulaw_reference(int(v)) for v in quantized]
    assert list(alaw_encode(samples)) == [alaw_reference(int(v)) for v in quantized]
    assert ulaw_encode(np.zeros(1, np.float32)) == b"\xff"
    assert alaw_encode(np.zeros(1, np.float32)) == b"\xd5"


def test_resample_keeps_duration_and_pitch():
    rate = 22050
    t = np.arange(rate) / rate
    tone = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    out = resample(tone, rate, 8000)
    assert out.size == 8000
    spectrum = np.abs(np.fft.rfft(out))
    assert abs(np.argmax(spectrum) * 8000 / out.size - 440) < 2


def test_wav_roundtrip():
    samples = np.sin(np.linspace(0, 50, 1000)).astype(np.float32) * 0.5
    back, rate = read_wav(wav_bytes(samples, 16000))
    assert rate == 16000 and np.allclose(back, samples, atol=1e-3)


# --- что и когда говорить ---

def seeds_event(level: str, previous: str | None = "ok", reminder: int = 0, **kw) -> SeedsLevelChanged:
    return SeedsLevelChanged(camera="Дробилка", camera_id="cam", level=level, previous=previous, duration_s=60,
                             reminder=reminder, **kw)


def test_phrase_for_levels():
    rules = SeedsConfig(say_empty="Пусто!", say_low="Мало", say_refill="", say_reminders=False)
    assert phrase_for(seeds_event("empty"), rules) == "Пусто!"
    assert phrase_for(seeds_event("empty", "empty", reminder=1), rules) == ""
    assert phrase_for(seeds_event("low"), rules) == "Мало"
    assert phrase_for(seeds_event("ok", "empty"), rules) == ""
    assert phrase_for(seeds_event("unknown"), rules) == ""


def test_in_hours_including_overnight():
    assert in_hours("", datetime(2026, 1, 1, 3, 0))
    assert in_hours("08:00-20:00", datetime(2026, 1, 1, 8, 0))
    assert not in_hours("08:00-20:00", datetime(2026, 1, 1, 20, 0))
    assert in_hours("22:00-06:00", datetime(2026, 1, 1, 23, 30))
    assert not in_hours("22:00-06:00", datetime(2026, 1, 1, 12, 0))
    with pytest.raises(ValueError):
        SeedsConfig(voice_hours="с утра")


def test_compose_repeats_with_chime_and_volume():
    speech = np.ones(22050, np.float32) * 0.1
    once, rate = compose(speech, 22050, VoiceConfig(chime=False, pause_s=0), 1)
    twice, _ = compose(speech, 22050, VoiceConfig(chime=True, pause_s=1, volume=0.5), 2)
    assert once.size == 22050 and twice.size > 3 * 22050
    assert np.max(np.abs(twice)) == pytest.approx(0.45, abs=0.01)


class FakeSpeaker:
    def __init__(self, name: str, error: str | None = None):
        self.name = name
        self.error = error
        self.played: list[int] = []

    def play(self, samples, rate):
        if self.error:
            raise SpeakerError(self.error)
        self.played.append(samples.size)


class FakeSynth:
    def __init__(self):
        self.texts: list[str] = []

    def synthesize(self, text):
        self.texts.append(text)
        return np.zeros(2205, np.float32), 22050


def test_announcer_plays_on_all_speakers_and_reports(monkeypatch):
    speakers = {"a": FakeSpeaker("Комната отдыха"), "b": FakeSpeaker("Цех", error="нет связи")}
    synth = FakeSynth()
    rules = SeedsConfig(say_times=2)
    results = []
    announcer = Announcer(lambda: synth, lambda: VoiceConfig(), lambda cid: (rules, list(speakers.values())),
                          on_result=lambda *r: results.append(r))
    announcer.start()
    try:
        announcer.handle(seeds_event("empty"))
        announcer.handle(seeds_event("ok", "empty"))  # фраза для «досыпали» пустая — молчим
        announcer.handle(seeds_event("empty", quiet=True))
        deadline = time.time() + 5
        while len(results) < 2 and time.time() < deadline:
            time.sleep(0.01)
    finally:
        announcer.stop()
    assert synth.texts == [rules.say_empty]
    assert len(speakers["a"].played) == 1
    assert sorted((r[1], r[2], r[3]) for r in results) == [
        ("динамик «Комната отдыха»", True, None), ("динамик «Цех»", False, "нет связи")]


def test_announcer_respects_hours():
    hour = datetime.now().hour
    rules = SeedsConfig(voice_hours=f"{(hour + 2) % 24:02d}:00-{(hour + 3) % 24:02d}:00")
    announcer = Announcer(FakeSynth, VoiceConfig, lambda cid: (rules, [FakeSpeaker("A")]))
    announcer.handle(seeds_event("empty"))
    assert announcer._queue.empty()


# --- динамик Hikvision против имитатора ISAPI ---

@dataclass
class FakeCamera:
    url_host: str = ""
    port: int = 0
    codec: str = "G.711ulaw"
    password: str = "secret"
    requests: list[tuple[str, str]] = field(default_factory=list)
    audio: bytes = b""
    audio_seconds: float = 0.0
    unauthorized: int = 0


@pytest.fixture
def camera() -> Iterator[FakeCamera]:
    state = FakeCamera()
    nonce = "abc123"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Digest "):
                return False
            p = dict(re.findall(r'(\w+)="?([^",]*)"?', header[7:]))
            ha1 = md5(f"admin:cam:{state.password}".encode()).hexdigest()
            ha2 = md5(f"{self.command}:{p['uri']}".encode()).hexdigest()
            expected = md5(f"{ha1}:{nonce}:{p['nc']}:{p['cnonce']}:{p['qop']}:{ha2}".encode()).hexdigest()
            return p.get("response") == expected and p["uri"] == self.path

        def _reply(self, status: int, body: bytes = b"", extra: dict | None = None):
            self.send_response(status)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self):
            started = time.monotonic()
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""
            if not self._authorized():
                state.unauthorized += 1
                self._reply(401, extra={"WWW-Authenticate": f'Digest realm="cam", nonce="{nonce}", qop="auth"'})
                return
            path = self.path.split("?")[0]
            state.requests.append((self.command, self.path))
            if path.endswith("/channels/1") and self.command == "GET":
                xml = (f'<TwoWayAudioChannel xmlns="http://www.hikvision.com/ver20/XMLSchema"><id>1</id>'
                       f"<enabled>false</enabled><audioCompressionType>{state.codec}</audioCompressionType>"
                       "</TwoWayAudioChannel>").encode()
                self._reply(200, xml)
            elif path.endswith("/open"):
                self._reply(200, b'<TwoWayAudioSession xmlns="urn:x"><sessionId>s42</sessionId></TwoWayAudioSession>')
            elif path.endswith("/audioData"):
                state.audio = body
                state.audio_seconds = time.monotonic() - started
                self._reply(200, b"<ResponseStatus/>")
            elif path.endswith("/close"):
                self._reply(200, b"<ResponseStatus/>")
            else:
                self._reply(404)

        do_GET = do_PUT = _handle

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield state
    server.shutdown()


def speaker_for(camera: FakeCamera, password: str = "secret") -> HikvisionSpeaker:
    return HikvisionSpeaker(SpeakerConfig(id="s", name="Комната отдыха", host="127.0.0.1", port=camera.port,
                                          username="admin", password=SecretStr(password), timeout_s=5))


def test_hikvision_two_way_audio_session(camera):
    speaker = speaker_for(camera)
    second = np.sin(np.linspace(0, 2000, 8000)).astype(np.float32) * 0.5
    started = time.monotonic()
    speaker.play(second, 8000)
    elapsed = time.monotonic() - started
    commands = [(m, p.split("/channels/1")[-1]) for m, p in camera.requests]
    assert commands == [("GET", ""), ("PUT", "/open"), ("PUT", "/audioData?sessionId=s42"),
                        ("PUT", "/close?sessionId=s42")]
    assert len(camera.audio) == 8000  # 1 с G.711 = 8000 байт
    assert camera.audio == ulaw_encode(second)
    assert camera.audio_seconds > 0.6  # звук отдан в темпе воспроизведения, а не разом
    assert elapsed >= 1.0  # и дождались, пока камера доиграет


def test_hikvision_alaw_and_unsupported_codec(camera):
    camera.codec = "G.711alaw"
    speaker_for(camera).play(np.zeros(800, np.float32), 8000)
    assert camera.audio == alaw_encode(np.zeros(800, np.float32))
    camera.codec = "G.726"
    with pytest.raises(SpeakerError, match="G.711ulaw"):
        speaker_for(camera).play(np.zeros(800, np.float32), 8000)


def test_hikvision_wrong_password_is_not_retried(camera):
    speaker = speaker_for(camera, password="wrong")
    with pytest.raises(SpeakerError, match="пароль"):
        speaker.play(np.zeros(800, np.float32), 8000)
    attempts = camera.unauthorized
    with pytest.raises(SpeakerError, match="повторных попыток не будет"):
        speaker.play(np.zeros(800, np.float32), 8000)
    assert camera.unauthorized == attempts  # камеру больше не дёргаем — иначе она заблокирует IP
    assert attempts <= 2


def test_hikvision_unreachable():
    speaker = HikvisionSpeaker(SpeakerConfig(id="s", host="127.0.0.1", port=1, timeout_s=1, password=SecretStr("x")))
    with pytest.raises(SpeakerError, match="нет связи"):
        speaker.probe()


def test_hikvision_without_password_never_contacts_camera(camera):
    speaker = HikvisionSpeaker(SpeakerConfig(id="s", host="127.0.0.1", port=camera.port, timeout_s=1))
    with pytest.raises(SpeakerError, match="не задан пароль"):
        speaker.play(np.zeros(800, np.float32), 8000)
    assert camera.unauthorized == 0 and camera.requests == []


def test_one_speaker_in_two_presets_plays_one_phrase_at_a_time(camera):
    """Обе дробилки говорят в одну камеру в комнате отдыха — сессии идут по очереди, а не вместе."""
    first, second = speaker_for(camera), speaker_for(camera)  # два пресета, один и тот же динамик
    errors = []

    def play(s):
        try:
            s.play(np.zeros(4000, np.float32), 8000)
        except SpeakerError as e:
            errors.append(str(e))

    threads = [threading.Thread(target=play, args=(s,)) for s in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    paths = [p.split("/channels/1")[-1].split("?")[0] for _, p in camera.requests]
    # open → audioData → close одной сессии, затем другой — без перекрытия
    sessions = [x for x in paths if x in ("/open", "/close")]
    assert sessions == ["/open", "/close", "/open", "/close"]
