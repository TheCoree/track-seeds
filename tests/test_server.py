import io
import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from track_seeds.analyzers.seeds import SeedsAnalyzer
from track_seeds.config import (
    CameraConfig,
    SeedSample,
    SeedsConfig,
    SeedZone,
    SpeakerConfig,
    VoiceConfig,
)
from track_seeds.crushers import CRUSHERS, CrusherSpec
from track_seeds.events import SeedsLevelChanged
from track_seeds.seeds import confidence
from track_seeds.server.app import create_app, masked, merge
from track_seeds.server.commands import _is_loopback
from track_seeds.server.store import ConfigStore, GlobalSettings
from track_seeds.server.workers import Supervisor

sys.path.insert(0, str(Path(__file__).parent))
from test_seeds import BOTTOM, scene

CSRF = {"X-Track-Seeds": "1"}
LOCAL = ("127.0.0.1", 50000)  # с этого компьютера: пока разработчика не завели, вход не нужен
DRIVE = [(0.76, 0.60), (0.96, 0.60), (0.96, 0.90), (0.76, 0.90)]


@pytest.fixture
def video(tmp_path: Path) -> Path:
    """Видео 2 с: бункер с семенами (проигрывается по кругу)."""
    path = tmp_path / "hopper.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (640, 360))
    for i in range(20):
        writer.write(scene(0.95, seed=i))
    writer.release()
    return path


def make_spec(tmp_path: Path, url: str, crusher_id: str = "drobilka-1", username: str = "") -> CrusherSpec:
    """Дробилка, как она была бы вшита в программу: камера — видеофайл, один вшитый эталон «есть»."""
    root = tmp_path / "builtin"
    (root / crusher_id / "samples").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(root / crusher_id / "samples/b1.jpg"), scene(0.95))
    return CrusherSpec(id=crusher_id, name=f"Дробилка {crusher_id[-1]}", camera_url=url, username=username,
                       zones=(SeedZone(id="z1", name="Дно", kind="empty", points=BOTTOM),
                              SeedZone(id="d", name="Привод", kind="motion", points=DRIVE)),
                       samples=(SeedSample(id="b1", image="samples/b1.jpg", ts=1, labels={"z1": "full"},
                                           builtin=True),),
                       builtin_root=root)


def new_store(root: Path, *specs: CrusherSpec) -> ConfigStore:
    store = ConfigStore(root, specs)
    store.save_settings(GlobalSettings(voice=VoiceConfig(engine="espeak")))  # без скачивания голоса piper в тестах
    store.load()
    return store


def make_client(tmp_path: Path, *specs: CrusherSpec, password: str | None = None) -> tuple[TestClient, Supervisor]:
    store = new_store(tmp_path / "config", *specs)
    sup = Supervisor(store, tmp_path / "data")
    sup.start()
    return TestClient(create_app(sup, None, password), headers=CSRF, client=LOCAL), sup


@pytest.fixture
def api(tmp_path):
    client, sup = make_client(tmp_path)
    yield client
    sup.stop()


@pytest.fixture
def crusher_api(tmp_path, video):
    client, sup = make_client(tmp_path, make_spec(tmp_path, str(video)))
    yield client, sup
    sup.stop()


def wait_for(fn, timeout: float = 15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if result := fn():
            return result
        time.sleep(0.1)
    raise AssertionError("не дождались")


def body_of(crusher: dict, **changes) -> dict:
    return {k: crusher[k] for k in ("enabled", "notify", "camera", "seeds", "speakers")} | changes


# --- дробилки предприятия ---

@pytest.mark.parametrize("spec", CRUSHERS, ids=lambda s: s.id)
def test_real_crusher_recognises_its_own_seeds(spec):
    """Настоящие эталоны дробилки: модель из трёх кадров узнаёт четвёртый как «семена есть» без сомнений."""
    zones = list(spec.zones)
    assert {z.kind for z in zones} == {"empty", "motion"} and all(0 <= c <= 1 for z in zones for p in z.points for c in p)
    assert len(spec.samples) >= 3 and all((spec.builtin_dir / s.image).is_file() for s in spec.samples)
    for left_out in spec.samples:
        rest = [s for s in spec.samples if s.id != left_out.id]
        analyzer = SeedsAnalyzer(SeedsConfig(zones=zones, samples=rest), spec.name, spec.builtin_dir,
                                 builtin_dir=spec.builtin_dir)
        (zone,) = analyzer.evaluate(cv2.imread(str(spec.builtin_dir / left_out.image)))
        assert zone["reading"] == "covered" and confidence(zone["score"]) > 0.99, (left_out.id, zone)


def test_confidence_keeps_the_boundary_but_not_the_doubt():
    assert confidence(0.5) == pytest.approx(0.5) and confidence(0.6, 0.6) == pytest.approx(0.5)
    assert confidence(0.75) > 0.98 and confidence(0.9) > 0.999 and confidence(0.2) < 0.01
    values = [confidence(s / 100) for s in range(1, 100)]
    assert values == sorted(values)


# --- настройки на месте ---

def test_local_settings_keep_passwords_apart_and_only_changes(tmp_path):
    spec = make_spec(tmp_path, "rtsp://10.0.0.8/Streaming/Channels/101", username="admin")
    store = new_store(tmp_path / "config", spec)

    def change(c):
        c.camera.password = SecretStr("cam-pw")
        c.speakers = [SpeakerConfig(id="s1", name="Комната отдыха", host="10.0.0.50", password=SecretStr("spk-pw"))]
        c.seeds.say_empty = "Пусто!"

    store.update("drobilka-1", change)
    folder = store.crusher_dir("drobilka-1")
    public = (folder / "settings.json").read_text()
    assert "cam-pw" not in public and "spk-pw" not in public
    data = json.loads(public)
    # только отличия от программы: адрес камеры, зоны и вшитые эталоны сюда не пишутся
    assert data["camera"] == {} and "zones" not in data["seeds"] and data["seeds"]["samples"] == []
    assert data["seeds"]["say_empty"] == "Пусто!" and "empty_after_s" not in data["seeds"]
    assert oct(os.stat(folder / "secrets.json").st_mode & 0o777) == "0o600"

    again = ConfigStore(tmp_path / "config", [spec])
    again.load()
    crusher = again.crusher("drobilka-1")
    assert crusher.camera.password.get_secret_value() == "cam-pw"
    assert crusher.speakers[0].password.get_secret_value() == "spk-pw"
    assert crusher.camera.url == spec.camera_url and crusher.seeds.zones == list(spec.zones)
    assert [s.id for s in crusher.seeds.samples] == ["b1"] and crusher.seeds.samples[0].builtin


def test_name_and_zones_stay_as_in_program(tmp_path):
    spec = make_spec(tmp_path, "x.mp4")
    store = new_store(tmp_path / "config", spec)

    def change(c):
        c.name = "Другая"
        c.seeds.zones = []

    saved = store.update("drobilka-1", change)
    assert saved.name == "Дробилка 1" and [z.id for z in saved.seeds.zones] == ["z1", "d"]


def test_broken_local_settings_fall_back_to_program(tmp_path):
    spec = make_spec(tmp_path, "x.mp4", username="admin")
    store = new_store(tmp_path / "config", spec)
    store.update("drobilka-1", lambda c: setattr(c.camera, "password", SecretStr("pw")))
    (store.crusher_dir("drobilka-1") / "settings.json").write_text("{oops")
    store.load()
    crusher = store.crusher("drobilka-1")
    assert "drobilka-1" in store.errors and crusher.camera.url == "x.mp4"
    assert crusher.camera.password.get_secret_value() == "pw"  # пароль не потерялся — камера работает


def test_old_presets_are_carried_over(tmp_path):
    spec = make_spec(tmp_path, "rtsp://10.0.0.8/101", username="admin")
    root = tmp_path / "config"
    old = root / "presets/drobilka-1"
    (old / "samples").mkdir(parents=True)
    cv2.imwrite(str(old / "samples/own.jpg"), scene(0.0))
    cv2.imwrite(str(old / "samples/b1.jpg"), scene(0.95))
    (old / "preset.json").write_text(json.dumps({
        "format": 1, "name": "Дробилка 1", "enabled": True, "notify": False,
        "camera": {"url": "rtsp://10.0.0.8/101", "username": "admin", "read_timeout_s": 7},
        "seeds": {"zones": [{"id": "old", "name": "Старая", "points": BOTTOM}], "say_empty": "Свои слова",
                  "samples": [{"id": "b1", "image": "samples/b1.jpg", "ts": 1, "labels": {"z1": "full"}},
                              {"id": "own", "image": "samples/own.jpg", "ts": 2, "labels": {"z1": "empty"}}]},
        "speakers": [{"id": "s1", "name": "Цех", "host": "10.0.0.5"}]}))
    (old / "secrets.json").write_text(json.dumps({"camera_password": "pw", "speakers": {"s1": "spw"}}))
    (root / "presets/sklad").mkdir()
    store = new_store(root, spec)
    crusher = store.crusher("drobilka-1")
    assert crusher.camera.password.get_secret_value() == "pw" and crusher.camera.read_timeout_s == 7
    assert crusher.speakers[0].password.get_secret_value() == "spw" and not crusher.notify
    assert crusher.seeds.say_empty == "Свои слова" and [z.id for z in crusher.seeds.zones] == ["z1", "d"]
    assert [(s.id, s.builtin) for s in crusher.seeds.samples] == [("b1", True), ("own", False)]
    assert (store.crusher_dir("drobilka-1") / "samples/own.jpg").is_file()
    assert not (root / "presets").exists() and list((root / ".trash").glob("presets-*/sklad"))


# --- API ---

def test_crusher_api(crusher_api, tmp_path):
    api, _ = crusher_api
    overview = api.get("/api/overview").json()
    assert [c["name"] for c in overview["crushers"]] == ["Дробилка 1"]
    crusher = overview["crushers"][0]
    assert crusher["camera"]["password"] is None and crusher["builtin_camera_url"].endswith("hopper.mp4")
    wait_for(lambda: api.get("/api/crushers/drobilka-1/frame.jpg").status_code == 200)

    body = body_of(crusher)
    body["seeds"]["zones"] = []  # зоны вшиты — из формы не меняются
    body["seeds"]["say_empty"] = "Пусто в первой!"
    body["camera"]["password"] = "p@ss"
    body["speakers"] = [{"name": "Комната отдыха", "kind": "hikvision", "host": "10.0.0.5", "password": "spw"}]
    saved = api.put("/api/crushers/drobilka-1", json=body).json()
    assert [z["id"] for z in saved["seeds"]["zones"]] == ["z1", "d"] and saved["seeds"]["say_empty"] == "Пусто в первой!"
    speaker = saved["speakers"][0]
    assert speaker["password"] is None and speaker["has_password"] and saved["camera"]["has_password"]

    # эталоны: свой — снять, переотметить, удалить; вшитый — только показать
    wait_for(lambda: api.get("/api/crushers/drobilka-1/frame.jpg").status_code == 200)  # пароль сменили — переподключение
    own = api.post("/api/crushers/drobilka-1/samples", json={"labels": {"z1": "full", "d": "full"}}).json()
    assert own["labels"] == {"z1": "full"} and not own["builtin"]  # привод без эталонов
    for sample_id in ("b1", own["id"]):
        assert api.get(f"/api/crushers/drobilka-1/samples/{sample_id}.jpg").headers["content-type"] == "image/jpeg"
    assert api.patch(f"/api/crushers/drobilka-1/samples/{own['id']}", json={"labels": {"z1": "empty"}}).json()["ok"]
    assert api.delete("/api/crushers/drobilka-1/samples/b1").status_code == 409
    assert api.patch("/api/crushers/drobilka-1/samples/b1", json={"labels": {"z1": "empty"}}).status_code == 409
    assert api.delete("/api/crushers/drobilka-1/samples/nope").status_code == 404

    png = cv2.imencode(".png", scene(0.0))[1].tobytes()
    upload = api.post("/api/crushers/drobilka-1/samples/upload?label=empty", content=png).json()
    assert upload["labels"] == {"z1": "empty"}
    wrong = cv2.imencode(".jpg", np.zeros((500, 500, 3), np.uint8))[1].tobytes()
    assert "Пропорции" in api.post("/api/crushers/drobilka-1/samples/upload?label=empty", content=wrong).json()["detail"]
    assert api.delete(f"/api/crushers/drobilka-1/samples/{own['id']}").json()["ok"]

    def zone():
        seeds = api.get("/api/crushers/drobilka-1").json()["status"].get("seeds") or {}
        zones = seeds.get("zones") or []
        return zones and zones[0]["samples"] == {"full": 1, "empty": 1} and zones[0]["confidence"] is not None and seeds

    seeds = wait_for(zone)
    assert seeds["zones"][0]["confidence"] > 0.99 and len(seeds["confidence_band"]) == 2

    # форма с устаревшим списком эталонов и без паролей ничего не теряет
    saved = api.put("/api/crushers/drobilka-1", json=body_of(saved, seeds=saved["seeds"] | {"samples": []})).json()
    assert [s["id"] for s in saved["seeds"]["samples"]] == ["b1", upload["id"]]
    secrets = json.loads((tmp_path / "config/crushers/drobilka-1/secrets.json").read_text())
    assert secrets["camera_password"] == "p@ss" and list(secrets["speakers"].values()) == ["spw"]

    assert api.put("/api/crushers/drobilka-1", json=body_of(saved, camera={"url": ""})).status_code == 422
    assert api.get("/api/crushers/nope").status_code == 404
    assert api.post("/api/cameras", json={"name": "Новая", "camera": {"url": "x"}}).status_code in (404, 405)


def test_changes_need_csrf_header(crusher_api):
    client, _ = crusher_api
    plain = TestClient(client.app, client=LOCAL)  # без заголовка — как форма с чужого сайта
    assert plain.post("/api/crushers/drobilka-1/samples", json={"labels": {"z1": "full"}}).status_code == 403
    assert plain.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code == 403
    response = plain.get("/api/meta")  # чтение — можно
    assert response.status_code == 200 and response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_password_protects_everything(tmp_path):
    """WEB_PASSWORD — разработчик admin: без входа ничего, кроме страницы входа и /api/health."""
    client, sup = make_client(tmp_path, password="s3cret-pw")
    try:
        assert client.get("/api/meta").status_code == 401
        assert client.get("/api/overview").status_code == 401
        assert client.get("/api/auth/info").json()["has_admin"]
        started = time.monotonic()
        wrong = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert wrong.status_code == 401 and wrong.json()["detail"] == "Неверный логин или пароль"
        assert time.monotonic() - started >= 0.9  # перебор паролей замедлен
        assert client.post("/api/auth/login", json={"username": "nobody", "password": "s3cret-pw"}).status_code == 401
        ok = client.post("/api/auth/login", json={"username": "Admin", "password": "s3cret-pw"})
        assert ok.status_code == 200 and ok.json()["role"] == "admin"
        cookies = ok.headers.get_list("set-cookie")
        assert all("HttpOnly" in c and "SameSite=strict" in c for c in cookies)
        assert any(c.startswith("ts_refresh=") and "Path=/api/auth" in c for c in cookies)
        assert client.get("/api/meta").status_code == 200
        health = TestClient(client.app, client=("8.8.8.8", 1)).get("/api/health")  # для HEALTHCHECK — без входа
        assert health.status_code == 200 and health.json()["ok"]
        client.post("/api/auth/logout").raise_for_status()
        assert client.get("/api/meta").status_code == 401
    finally:
        sup.stop()


def test_network_access_requires_password():
    assert _is_loopback("127.0.0.1") and _is_loopback("localhost") and _is_loopback("::1")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("10.20.10.5")


def test_events_logged_with_speech_and_sample_from_event(crusher_api):
    client, sup = crusher_api
    crusher = client.get("/api/crushers/drobilka-1").json()
    client.put("/api/crushers/drobilka-1",
               json=body_of(crusher, seeds=crusher["seeds"] | {"say_empty": "Пусто!"})).raise_for_status()
    frame = cv2.imencode(".jpg", scene(0.0))[1].tobytes()
    sup.bus.publish(SeedsLevelChanged(camera="Дробилка 1", camera_id="drobilka-1", level="empty", previous="ok",
                                      duration_s=60, zones=("Дно",), snapshot=frame, frame=frame))
    event = wait_for(lambda: client.get("/api/events", params={"camera_id": "drobilka-1"}).json())[0]
    assert event["title"] == "Дробилка 1: закончились семена" and event["speech"] == "Пусто!"
    assert client.get(f"/api/files/{event['snapshot']}").status_code == 200
    sample = client.post("/api/crushers/drobilka-1/samples", json={"labels": {"z1": "empty"},
                                                                    "event_uid": event["uid"]}).json()
    assert sample["labels"] == {"z1": "empty"}
    assert client.get("/api/files/../config/settings.json").status_code == 404


def test_settings_hide_tokens(api):
    saved = api.put("/api/settings", json={"ntfy": {"topic": "seeds-x", "token": "tk_123"},
                                            "notify": {"channels": ["log", "ntfy"]}}).json()
    assert saved["ntfy"]["token"] is None and saved["ntfy"]["has_token"] and saved["notify_error"] is None
    assert api.put("/api/settings", json={"ntfy": saved["ntfy"]}).json()["ntfy"]["has_token"]
    assert api.put("/api/settings", json={"notify": {"channels": ["pigeon"]}}).status_code == 422
    assert api.put("/api/settings", json={"voice": {"voice": "../../etc"}}).status_code == 422


def test_voice_preview_is_never_cached_and_names_the_voice(api, monkeypatch):
    from track_seeds.voice import Synthesizer

    monkeypatch.setattr(Synthesizer, "synthesize", lambda self, text: (np.zeros(2205, np.float32), 22050))
    monkeypatch.setattr(Synthesizer, "_pick_engine", lambda self: "piper")
    monkeypatch.setattr(Synthesizer, "prepare", lambda self: None)
    api.put("/api/settings", json={"voice": {"voice": "ru_RU-irina-medium", "engine": "auto"}}).raise_for_status()
    response = api.get("/api/voice/say.wav", params={"text": "Проверка"})
    assert response.headers["cache-control"] == "no-store" and response.headers["x-voice"] == "ru_RU-irina-medium"
    api.put("/api/settings", json={"voice": {"voice": "ru_RU-denis-medium"}}).raise_for_status()
    assert api.get("/api/voice/say.wav", params={"text": "Проверка"}).headers["x-voice"] == "ru_RU-denis-medium"


def test_masking_and_merge_semantics():
    current = CameraConfig(url="rtsp://admin:secret@h:554/1", password=SecretStr("pw"))
    out = masked(current)
    assert out["password"] is None and out["has_password"] and "secret" not in out["url"]
    same = merge(current, out, CameraConfig)
    assert same.url == current.url and same.password.get_secret_value() == "pw"
    assert merge(current, {"password": ""}, CameraConfig).password is None


def test_lifespan_starts_and_stops_everything(tmp_path):
    store = new_store(tmp_path / "config")
    sup = Supervisor(store, tmp_path / "data")
    with TestClient(create_app(sup, None, None, manage=True)) as client:
        assert client.get("/api/health").json()["event_bus"]
    assert not sup.bus.alive and not sup.announcer.alive


# --- консольные команды ---

def run_cli(tmp_path: Path, monkeypatch, specs, *argv: str) -> int:
    from track_seeds.cli import build_parser
    from track_seeds.config import load_settings

    monkeypatch.setattr("track_seeds.crushers.CRUSHERS", tuple(specs))  # не трогать настоящие камеры
    settings = load_settings(None)
    settings.app.config_dir = tmp_path / "config"
    settings.app.data_dir = tmp_path / "data"
    args = build_parser().parse_args(["--env-file", "/nonexistent", *argv])
    return args.func(settings, args)


def test_cli_crushers_check_and_password(tmp_path, video, capsys, monkeypatch):
    specs = [make_spec(tmp_path, str(video))]
    new_store(tmp_path / "config", *specs)
    assert run_cli(tmp_path, monkeypatch, specs, "crushers") == 0
    out = capsys.readouterr().out
    assert "Дробилка 1" in out and "Дно, Привод" in out and "эталонов 1" in out

    run_cli(tmp_path, monkeypatch, specs, "check")
    out = capsys.readouterr().out
    assert "✓ камера: 640×360" in out and "зона «Дно»: семена есть" in out and "привод «Привод»" in out

    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # не терминал — пароль не спрашиваем
    with pytest.raises(SystemExit, match="в терминале"):
        run_cli(tmp_path, monkeypatch, specs, "password")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "new-pw")
    assert run_cli(tmp_path, monkeypatch, specs, "password", "drobilka-1") == 0
    secrets = json.loads((tmp_path / "config/crushers/drobilka-1/secrets.json").read_text())
    assert secrets["camera_password"] == "new-pw"


def test_cli_say_on_local_speaker(tmp_path, monkeypatch):
    from track_seeds.voice import Synthesizer
    from track_seeds.voice import speakers as speakers_module

    played = []
    monkeypatch.setattr(Synthesizer, "synthesize", lambda self, text: (np.zeros(2205, np.float32), 22050))
    monkeypatch.setattr(speakers_module.LocalSpeaker, "play", lambda self, a, r: played.append(self.name))
    specs = [make_spec(tmp_path, "x.mp4")]
    store = new_store(tmp_path / "config", *specs)
    store.update("drobilka-1", lambda c: setattr(c, "speakers", [
        SpeakerConfig(id="l", name="Динамик в цеху", kind="local", device="plughw:1,0")]))
    assert run_cli(tmp_path, monkeypatch, specs, "say", "Проверка", "--crusher", "drobilka-1") == 0
    assert played == ["Динамик в цеху"]


def test_camera_without_password_is_not_hammered(tmp_path):
    """Пароль камеры ещё не задан — не подключаемся, иначе камера заблокирует IP за неверные входы."""
    from track_seeds.server.workers import CameraWorker

    store = new_store(tmp_path / "config", make_spec(tmp_path, "rtsp://10.0.0.8/102", username="admin"))
    worker = CameraWorker(store.crusher("drobilka-1"), store.settings, store, None)  # type: ignore[arg-type]
    worker.start()
    assert worker.state == "error" and "пароль" in worker.error and worker._thread is None


def test_only_local_network_is_let_in(tmp_path):
    from track_seeds.config import ServerSettings

    networks = ServerSettings(_env_file=None).allowed_networks
    store = new_store(tmp_path / "config")
    sup = Supervisor(store, tmp_path / "data")
    app = create_app(sup, None, "pw-12345678", allowed_networks=networks)
    for address in ("192.168.1.57", "10.0.0.3", "127.0.0.1", "::ffff:192.168.1.57"):
        assert TestClient(app, client=(address, 5000)).get("/api/auth/info").status_code == 200, address
    for address in ("8.8.8.8", "testclient"):
        response = TestClient(app, client=(address, 5000)).get("/api/health")
        assert response.status_code == 403 and "локальной сети" in response.json()["detail"], address
    with pytest.raises(ValueError, match="не сеть"):
        ServerSettings(_env_file=None, allowed_networks="192.168.1.0/24,интернет")
    assert ServerSettings(_env_file=None, allowed_networks="any").allowed_networks == ["0.0.0.0/0", "::/0"]


def test_events_are_filtered_by_kind(tmp_path):
    """Журнал: «закончились», «остановилась», «запустилась»… — фильтр и числа на кнопках; проверки — не клиентам."""
    from track_seeds.events import CameraOffline
    from track_seeds.server.eventlog import EventLog

    log = EventLog(tmp_path / "log")
    seeds = {"camera": "Дробилка 1", "camera_id": "drobilka-1", "duration_s": 60}
    for level, previous, extra in [("empty", "ok", {}), ("empty", "empty", {"reminder": 1}), ("ok", "empty", {}),
                                   ("stopped", "ok", {}), ("ok", "stopped", {}), ("unknown", "ok", {}),
                                   ("low", "ok", {}), ("empty", "ok", {"test": True})]:
        log.record(SeedsLevelChanged(level=level, previous=previous, **seeds, **extra))
    log.record(CameraOffline(camera="Дробилка 2", camera_id="drobilka-2", since=0))
    assert log.counts() == {"empty": 1, "reminder": 1, "refill": 1, "stopped": 1, "started": 1, "unknown": 1,
                            "low": 1, "test": 1, "offline": 1}
    assert "test" not in log.counts(tests=False) and log.counts("drobilka-2") == {"offline": 1}
    rows = log.query(kinds=["stopped", "started"])
    assert [(r["kind"], r["data"]["previous"]) for r in rows] == [("started", "stopped"), ("stopped", "ok")]
    assert [r["kind"] for r in log.query(kinds=["empty"], tests=False)] == ["empty"]  # проверка того же вида — нет
    log.close()

    # API: клиенту — без проверок и с теми же фильтрами
    client, sup = make_client(tmp_path)
    try:
        sup.events.record(SeedsLevelChanged(level="stopped", previous="ok", **seeds))
        sup.events.record(SeedsLevelChanged(level="empty", previous="ok", test=True, **seeds))
        assert client.get("/api/events/kinds").json() == {"stopped": 1, "test": 1}
        assert [e["kind"] for e in client.get("/api/events", params={"kind": ["stopped", "test"]}).json()] == [
            "test", "stopped"]
    finally:
        sup.stop()
