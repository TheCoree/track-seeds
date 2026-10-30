"""Аккаунты и доступ: разработчик, клиенты по приглашению, токены, 2FA, виджет; отчёты о работе; живое видео."""

import ipaddress
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from track_seeds.config import LOCAL_NETWORKS
from track_seeds.events import SeedsLevelChanged
from track_seeds.server import auth as auth_module
from track_seeds.server.app import create_app
from track_seeds.server.auth import (
    AccountStore,
    jwt_decode,
    jwt_encode,
    totp_code,
    totp_match,
)
from track_seeds.server.workers import Supervisor
from track_seeds.server.worklog import WorkLog, report

sys.path.insert(0, str(Path(__file__).parent))
from test_server import CSRF, LOCAL, make_spec, new_store, video, wait_for  # noqa: F401 — video: фикстура

ADMIN_PW = "dev-Pass-2026"
OUTSIDE = ("8.8.8.8", 40000)
LAN = ("192.168.1.50", 40000)


@pytest.fixture
def site(tmp_path, video):  # noqa: F811 — фикстура из test_server
    """Сервер как на проде: разработчик из WEB_PASSWORD, сайт открыт в интернет, панель — только из локальной сети."""
    store = new_store(tmp_path / "config", make_spec(tmp_path, str(video)))
    sup = Supervisor(store, tmp_path / "data")
    sup.start()
    app = create_app(sup, None, ADMIN_PW, allowed_networks=["0.0.0.0/0", "::/0"], admin_networks=LOCAL_NETWORKS)
    yield app, sup
    sup.stop()


def browser(app, address=LAN, headers=CSRF) -> TestClient:
    if not ipaddress.ip_address(address[0]).is_private:  # из интернета — как на проде: через Caddy, по HTTPS
        return TestClient(app, base_url="https://seeds.example.ru", client=("127.0.0.1", address[1]),
                          headers=headers | {"X-Forwarded-For": address[0], "X-Forwarded-Proto": "https"})
    return TestClient(app, headers=headers, client=address)


def login(client: TestClient, username: str, password: str, code: str = "") -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password, "code": code})
    assert response.status_code == 200, response.text
    return response.json()


def invite(admin: TestClient, note: str = "Иван, мастер смены") -> str:
    response = admin.post("/api/admin/invites", json={"note": note, "hours": 24})
    assert response.status_code == 200, response.text
    return response.json()["code"]


def register(client: TestClient, code: str, username: str = "ivan", password: str = "Seeds-2026!") -> dict:
    response = client.post("/api/auth/register",
                           json={"code": code, "username": username, "name": "Иван", "password": password})
    assert response.status_code == 200, response.text
    return response.json()


PUBLIC = {"/api/health", "/api/auth/info", "/api/auth/login", "/api/auth/refresh", "/api/auth/logout",
          "/api/auth/invite", "/api/auth/register", "/api/auth/setup", "/api/{path:path}"}


def test_every_api_route_needs_sign_in_except_public_ones(site):
    app, _ = site
    stranger = browser(app, OUTSIDE)
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/") or route.path in PUBLIC:
            continue
        path = route.path.replace("{crusher_id}", "drobilka-1").replace("{path:path}", "x")
        path = path.replace("{", "").replace("}", "")
        for method in route.methods:
            response = stranger.request(method, path)
            assert response.status_code == 401, (method, route.path, response.status_code)


def test_client_registers_by_one_time_link_and_sees_only_monitoring(site):
    app, sup = site
    admin = browser(app)
    assert login(admin, "admin", ADMIN_PW)["role"] == "admin"
    code = invite(admin)

    phone = browser(app, OUTSIDE)
    info = phone.get("/api/auth/invite", params={"code": code}).json()
    assert info["note"] == "Иван, мастер смены" and not info["reset"]
    me = register(phone, code)
    assert me["role"] == "client" and me["name"] == "Иван"
    # ссылка — одному человеку
    again = browser(app, OUTSIDE).post("/api/auth/register", json={"code": code, "username": "petr",
                                                                     "password": "Other-pass-1"})
    assert again.status_code == 410

    overview = phone.get("/api/overview").json()
    crusher = overview["crushers"][0]
    assert crusher["name"] == "Дробилка 1" and crusher["seeds"]["zones"]
    for hidden in ("camera", "speakers", "folder", "builtin_camera_url"):
        assert hidden not in crusher
    assert "notify_error" not in overview
    for path in ("/api/meta", "/api/settings", "/api/admin/users", "/api/crushers/drobilka-1/snapshot.jpg",
                 "/api/voice/say.wav?text=x", "/api/crushers/drobilka-1/samples/b1.jpg"):
        assert phone.get(path).status_code == 403, path
    assert phone.put("/api/crushers/drobilka-1", json={"camera": {}}).status_code == 403
    assert phone.post("/api/crushers/drobilka-1/test-alarm").status_code == 403

    # проверка оповещений разработчиком клиенту не видна, доставка уведомлений — тоже
    admin.post("/api/crushers/drobilka-1/test-alarm").raise_for_status()
    sup.bus.publish(SeedsLevelChanged(camera="Дробилка 1", camera_id="drobilka-1", level="stopped", previous="ok",
                                      duration_s=60))
    wait_for(lambda: len(admin.get("/api/events").json()) >= 2)
    events = phone.get("/api/events").json()
    assert [e["data"]["level"] for e in events] == ["stopped"] and events[0]["deliveries"] == []

    users = {u["username"]: u for u in admin.get("/api/admin/users").json()}
    assert users["ivan"]["role"] == "client" and users["ivan"]["sessions"] == 1
    invites = admin.get("/api/admin/invites").json()["invites"]
    assert invites[0]["used"] and invites[0]["used_login"] == "ivan"


def test_developer_panel_only_from_local_network(site):
    app, _ = site
    outside = browser(app, OUTSIDE)
    started = time.monotonic()
    response = outside.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert response.status_code == 401 and time.monotonic() - started >= 0.9  # пароль даже не проверяется
    assert not outside.get("/api/auth/info").json()["admin_here"]
    assert outside.get("/api/health").json() == {"ok": True}  # подробности о дробилках — только своим

    # за обратным прокси на этом же сервере адрес посетителя — из X-Forwarded-For
    proxied = TestClient(app, headers=CSRF | {"X-Forwarded-For": "8.8.8.8", "X-Forwarded-Proto": "https"},
                         client=("127.0.0.1", 1))
    response = proxied.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert response.status_code == 401
    assert proxied.get("/api/health").headers["strict-transport-security"]
    # напрямую на порт сервера из интернета — отказ, даже с подделанным X-Forwarded-For
    spoofed = TestClient(app, headers=CSRF | {"X-Forwarded-For": "192.168.1.5"}, client=OUTSIDE)
    response = spoofed.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert response.status_code == 403 and "HTTPS" in response.json()["detail"]
    # посетитель прислал свой X-Forwarded-For, прокси дописал настоящий адрес — берётся настоящий
    chained = TestClient(app, headers=CSRF | {"X-Forwarded-For": "192.168.1.5, 8.8.8.8"}, client=("127.0.0.1", 1))
    assert chained.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 401

    lan = browser(app)
    login(lan, "admin", ADMIN_PW)
    assert lan.get("/api/admin/users").status_code == 200


def test_refresh_rotates_and_reuse_closes_the_session(site, monkeypatch):
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    phone = browser(app, OUTSIDE)
    register(phone, invite(admin))
    old = phone.cookies.get("ts_refresh", path="/api/auth")

    response = phone.post("/api/auth/refresh")
    assert response.status_code == 200
    new = phone.cookies.get("ts_refresh", path="/api/auth")
    assert new and new != old

    # соседняя вкладка прислала прежний токен сразу после смены — пускаем, новый не выдаём
    tab = browser(app, OUTSIDE)
    tab.cookies.set("ts_refresh", old, path="/api/auth")
    assert tab.post("/api/auth/refresh").status_code == 200
    assert "ts_refresh=" not in tab.post("/api/auth/refresh").headers.get("set-cookie", "")

    # тот же старый токен через долгое время — кража: сессия закрывается целиком
    monkeypatch.setattr(auth_module, "REFRESH_GRACE_S", 0)
    thief = browser(app, OUTSIDE)
    thief.cookies.set("ts_refresh", old, path="/api/auth")
    assert thief.post("/api/auth/refresh").status_code == 401
    assert phone.get("/api/overview").status_code == 401  # и access-токен владельца больше не действует
    assert phone.post("/api/auth/refresh").status_code == 401


def test_disabled_client_loses_access_at_once(site):
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    phone = browser(app, OUTSIDE)
    me = register(phone, invite(admin))
    assert phone.get("/api/overview").status_code == 200
    admin.patch(f"/api/admin/users/{me['user_id']}", json={"disabled": True}).raise_for_status()
    assert phone.get("/api/overview").status_code == 401
    response = browser(app, OUTSIDE).post("/api/auth/login", json={"username": "ivan", "password": "Seeds-2026!"})
    assert response.status_code == 403 and "отключён" in response.json()["detail"]
    assert admin.delete(f"/api/admin/users/{me['user_id']}").status_code == 200
    me_admin = admin.get("/api/auth/me").json()
    assert admin.delete(f"/api/admin/users/{me_admin['user_id']}").status_code == 409  # себя — нет


def test_password_change_and_reset_link(site):
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    phone = browser(app, OUTSIDE)
    me = register(phone, invite(admin))
    laptop = browser(app, OUTSIDE)
    login(laptop, "ivan", "Seeds-2026!")

    assert phone.post("/api/auth/password", json={"current": "nope", "new": "Brand-new-1"}).status_code == 403
    assert phone.post("/api/auth/password", json={"current": "Seeds-2026!", "new": "123"}).status_code == 422
    phone.post("/api/auth/password", json={"current": "Seeds-2026!", "new": "Brand-new-1"}).raise_for_status()
    assert phone.get("/api/overview").status_code == 200  # эта сессия осталась
    assert laptop.get("/api/overview").status_code == 401  # остальные закрыты

    code = admin.post(f"/api/admin/users/{me['user_id']}/reset", json={"hours": 1}).json()["code"]
    guest = browser(app, OUTSIDE)
    assert guest.get("/api/auth/invite", params={"code": code}).json() == {
        "note": "новый пароль", "expires": pytest.approx(time.time() + 3600, abs=5), "reset": True,
        "username": "ivan"}
    register(guest, code, username="", password="After-reset-2")
    login(browser(app, OUTSIDE), "ivan", "After-reset-2")


def test_too_many_wrong_passwords_lock_the_address(site):
    app, _ = site
    accounts: AccountStore = app.state.accounts
    for _ in range(10):
        accounts.throttle.fail("8.8.8.8", "admin")
    response = browser(app, OUTSIDE).post("/api/auth/login", json={"username": "ivan", "password": "x"})
    assert response.status_code == 429 and "мин" in response.json()["detail"]
    assert browser(app, ("9.9.9.9", 1)).post("/api/auth/login", json={"username": "ivan", "password": "x"}
                                             ).status_code == 401  # другой адрес — своя очередь


def test_two_factor_login(site):
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    other = browser(app)
    login(other, "admin", ADMIN_PW)
    setup = admin.post("/api/auth/totp/setup").json()
    assert setup["uri"].startswith("otpauth://totp/track-seeds:admin?secret=") and setup["qr"].startswith("<svg")
    assert admin.post("/api/auth/totp/enable", json={"code": "000000"}).status_code == 422
    step = int(time.time() // 30)
    admin.post("/api/auth/totp/enable", json={"code": totp_code(setup["secret"], step)}).raise_for_status()
    assert admin.get("/api/auth/me").json()["totp"]
    assert other.get("/api/meta").status_code == 401  # сессии без кода закрыты

    lan = browser(app)
    response = lan.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert response.status_code == 401 and response.json()["code"] == "totp"
    response = lan.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW, "code": "123456"})
    assert response.status_code == 401 and response.json()["code"] == "totp"
    code = totp_code(setup["secret"], step + 1)
    login(lan, "admin", ADMIN_PW, code)
    replay = browser(app).post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW, "code": code})
    assert replay.status_code == 401  # тот же код второй раз не проходит

    assert admin.post("/api/auth/totp/disable", json={"password": "wrong"}).status_code == 403
    admin.post("/api/auth/totp/disable", json={"password": ADMIN_PW}).raise_for_status()
    login(browser(app), "admin", ADMIN_PW)


def test_widget_key_reads_status_only(site):
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    phone = browser(app, OUTSIDE)
    register(phone, invite(admin))
    created = phone.post("/api/auth/widgets", json={"name": "iPhone Ивана"}).json()
    token = created["token"]
    assert token.startswith("tsw_")

    widget = browser(app, OUTSIDE, {"Authorization": f"Bearer {token}"})
    data = wait_for(lambda: (d := widget.get("/api/widget").json())["crushers"][0]["state"] and d)
    crusher = data["crushers"][0]
    assert crusher["name"] == "Дробилка 1" and crusher["label"] and set(crusher["today"]) >= {"running", "stopped"}
    assert widget.get("/api/widget/drobilka-1.jpg").headers["content-type"] == "image/jpeg"
    for path in ("/api/overview", "/api/auth/me", "/api/events", "/api/meta"):
        assert widget.get(path).status_code == 401, path
    assert phone.get("/api/auth/widgets").json()[0]["last_used"]

    phone.delete(f"/api/auth/widgets/{created['widget']['id']}").raise_for_status()
    assert widget.get("/api/widget").status_code == 401


def test_bootstrap_password_from_env_and_open_mode(tmp_path):
    store = new_store(tmp_path / "config")
    sup = Supervisor(store, tmp_path / "data")
    # разработчика нет — с этого компьютера без входа, из сети — нет
    app = create_app(sup, None, None)
    assert TestClient(app, client=LOCAL).get("/api/meta").status_code == 200
    assert TestClient(app, client=LAN).get("/api/meta").status_code == 401
    local = TestClient(app, headers=CSRF, client=LOCAL)
    assert local.get("/api/auth/me").json()["open"]
    local.post("/api/auth/setup", json={"username": "nova", "password": "Nova-dev-2026"}).raise_for_status()
    assert TestClient(app, client=LOCAL).get("/api/meta").status_code == 401  # теперь — только со входом
    assert local.get("/api/auth/me").json()["username"] == "nova"
    assert local.post("/api/auth/setup", json={"username": "x2", "password": "Nova-dev-2026"}).status_code == 409

    accounts = AccountStore(tmp_path / "data2")
    assert "создан" in accounts.bootstrap_admin("first-pass-1")
    assert accounts.bootstrap_admin("first-pass-1") is None  # тот же пароль в .env — ничего не меняем
    user = accounts.by_login("admin")
    session_id, _ = accounts.new_session(user["id"], "admin")
    assert "сброшен" in accounts.bootstrap_admin("second-pass-2")
    assert not accounts.session_alive(session_id)
    accounts.login("admin", "second-pass-2")
    assert (tmp_path / "data2/accounts.db").stat().st_mode & 0o777 == 0o600


def test_tokens_and_codes():
    key = b"k" * 32
    token = jwt_encode({"sub": 1, "exp": time.time() + 60}, key)
    assert jwt_decode(token, key)["sub"] == 1
    head, _, sig = token.split(".")
    forged = jwt_encode({"sub": 2, "exp": time.time() + 60}, b"x" * 32).split(".")[1]
    assert jwt_decode(f"{head}.{forged}.{sig}", key) is None
    assert jwt_decode(jwt_encode({"sub": 1, "exp": time.time() - 1}, key), key) is None
    assert jwt_decode("garbage", key) is None
    # RFC 6238, приложение B: секрет «12345678901234567890», T = 59 с → 94287082 (8 цифр)
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, 1, 8) == "94287082"
    assert totp_match(secret, "287082", 0, now=59) == 1 and totp_match(secret, "287082", 1, now=59) is None


def test_live_stream_sends_camera_frames(site):
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    wait_for(lambda: admin.get("/api/crushers/drobilka-1").json()["status"]["connected"])
    with admin.stream("GET", "/api/crushers/drobilka-1/live.mjpg") as response:
        assert response.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert response.headers["x-accel-buffering"] == "no"  # прокси на nginx не копят поток в буфере
        chunk = b""
        for part in response.iter_bytes():
            chunk += part
            if chunk.count(b"\xff\xd9") >= 2:
                break
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg") and b"\xff\xd8" in chunk


def test_live_by_frame_when_stream_is_buffered(site):
    """Поток не доходит через прокси — браузер берёт кадры по одному: каждый новее прошлого, клиенту тоже,
    без входа — нет."""
    app, _ = site
    admin = browser(app)
    login(admin, "admin", ADMIN_PW)
    wait_for(lambda: admin.get("/api/crushers/drobilka-1").json()["status"]["connected"])
    first = admin.get("/api/crushers/drobilka-1/live.jpg")
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    assert first.content.startswith(b"\xff\xd8")
    seq = int(first.headers["x-frame-seq"])
    second = admin.get("/api/crushers/drobilka-1/live.jpg", params={"after": seq})
    assert second.status_code in (200, 204)
    if second.status_code == 200:
        assert int(second.headers["x-frame-seq"]) != seq
    phone = browser(app, OUTSIDE)
    register(phone, invite(admin))
    assert phone.get("/api/crushers/drobilka-1/live.jpg").status_code == 200
    assert TestClient(app, client=LAN).get("/api/crushers/drobilka-1/live.jpg").status_code == 401


# --- отчёты ---

def test_report_counts_work_stops_and_gaps(tmp_path):
    log = WorkLog(tmp_path)
    base = datetime(2026, 10, 30, 8, 0).timestamp()

    def run(state: str, start_min: float, end_min: float) -> None:
        t = start_min * 60
        while t <= end_min * 60:
            log.record("d1", state, base + t)
            t += 5

    run("running", 0, 60)  # 08:00–09:00 работает
    run("stopped", 60.1, 90)  # 09:00–09:30 стоит — остановка
    run("running", 90.1, 120)  # 09:30–10:00
    run("idle", 120.1, 130)  # 10:00–10:10 крутится без зерна
    run("running", 130.1, 140)
    run("stopped", 200, 260)  # 11:20–12:20 стоит после разрыва (сервер выключали) — не новая остановка
    now = base + 260 * 60
    day = report(log, [("d1", "Дробилка 1")], "day", date(2026, 10, 30), now)["crushers"][0]
    hours = {k: round(v / 60) for k, v in day["totals"].items()}
    assert hours == {"running": 100, "idle": 10, "stopped": 90, "offline": 0, "unknown": 0,
                     "nodata": 8 * 60 + 60}  # до 08:00 и разрыв 10:20–11:20
    assert day["stops"] == 1 and day["runouts"] == 1 and len(day["stop_list"]) == 2
    assert round(day["longest_work"] / 60) == 60  # 08:00–09:00; 09:30–10:20 вместе с «без зерна» — 50 мин
    assert datetime.fromtimestamp(day["first_work"]).strftime("%H:%M") == "08:00"
    assert abs(day["last_work"] - (base + 140 * 60)) < 10  # 10:20
    assert round(day["buckets"][8]["running"] / 60) == 60 and round(day["buckets"][9]["stopped"] / 60) == 30
    assert [s for s, *_ in day["timeline"]] == ["running", "stopped", "running", "idle", "running", "stopped"]

    week = report(log, [("d1", "Дробилка 1")], "week", date(2026, 10, 30), now)
    assert len(week["crushers"][0]["buckets"]) == 7 and week["next"] is None
    assert round(week["crushers"][0]["buckets"][4]["running"] / 60) == 100  # пятница
    assert report(log, [("d1", "Д")], "month", date(2026, 10, 1), now)["prev"] == "2026-09-01"
    log.close()


def test_developer_panel_from_internet_when_allowed(tmp_path, video):  # noqa: F811 — фикстура из test_server
    """WEB_ADMIN_NETWORKS=any: разработчик входит и из интернета (через Caddy); подробности /api/health
    без входа — по-прежнему только из локальной сети; «без пароля» вместе с any сервер не принимает."""
    from track_seeds.config import ServerSettings

    store = new_store(tmp_path / "config", make_spec(tmp_path, str(video)))
    sup = Supervisor(store, tmp_path / "data")
    sup.start()
    try:
        anywhere = ["0.0.0.0/0", "::/0"]
        app = create_app(sup, None, ADMIN_PW, allowed_networks=anywhere, admin_networks=anywhere)
        outside = browser(app, OUTSIDE)
        assert outside.get("/api/auth/info").json()["admin_here"]
        login(outside, "admin", ADMIN_PW)
        assert outside.get("/api/meta").status_code == 200  # настройки — только разработчику
        assert outside.get("/api/health").json() == {"ok": True}
        assert "crushers" in browser(app).get("/api/health").json()
    finally:
        sup.stop()

    assert ServerSettings(_env_file=None, admin_networks="any").admin_networks == ["0.0.0.0/0", "::/0"]
    with pytest.raises(ValueError, match="WEB_ALLOW_NO_PASSWORD"):
        ServerSettings(_env_file=None, admin_networks="any", allow_no_password=True)


def test_password_guessing_from_internet_does_not_lock_out_the_office(site):
    """Перебор пароля admin с сотни адресов интернета закрывает вход на этот логин из интернета, но не из
    локальной сети: иначе любой мог бы запирать разработчика (с WEB_ADMIN_NETWORKS=any — и снаружи)."""
    app, _ = site
    accounts: AccountStore = app.state.accounts
    for i in range(31):
        accounts.throttle.fail(f"5.6.7.{i}", "admin")
    response = browser(app, ("9.9.9.9", 1)).post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert response.status_code == 429  # из интернета — ждать
    login(browser(app), "admin", ADMIN_PW)  # из офиса — входит
