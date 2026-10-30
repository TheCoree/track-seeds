"""HTTP API веб-интерфейса и раздача собранного фронтенда (Next.js, статический экспорт).

Кто что может:
- разработчик (admin) — всё, но только из WEB_ADMIN_NETWORKS (по умолчанию — локальная сеть),
  даже когда сайт открыт клиентам в интернет;
- клиент (client) — дробилки в реальном времени, события, отчёты; настроек, паролей и адресов камер не видит;
- ключ виджета — только /api/widget.
Каждый маршрут API подключён к одному из роутеров public / viewer / admin — без роутера маршрута нет,
по умолчанию нужен разработчик.

Вход — логин, пароль и (по желанию) код 2FA → access-токен (JWT, 15 мин) и refresh-токен в cookie
HttpOnly + SameSite=Strict; для своих программ — заголовок Authorization: Bearer <access>.
Пока разработчика не завели, с этого компьютера (127.0.0.1) пускает без входа, как раньше.
Сайт — только из WEB_ALLOWED_NETWORKS, изменяющие запросы — только с заголовком X-Track-Seeds (CSRF),
защитные заголовки, ограничения на размер загрузок; пароли камер и динамиков наружу не отдаются.
"""

import asyncio
import base64
import ipaddress
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..camera import mask_url, probe_camera
from ..config import (
    LOCAL_NETWORKS,
    LOOPBACK,
    CameraConfig,
    NotifyConfig,
    NtfyConfig,
    SeedsConfig,
    SpeakerConfig,
    VoiceConfig,
)
from ..events import SeedsLevelChanged
from ..notify import CHANNELS, Notification
from ..voice import VOICES, SpeakerError, compose, espeak_installed, piper_installed
from ..voice.audio import wav_bytes
from . import worklog
from .auth import (
    ACCESS_TTL_S,
    INVITE_HOURS,
    REFRESH_TTL_S,
    WIDGET_PREFIX,
    AccountStore,
    AuthError,
    Principal,
    qr_svg,
)
from .store import Crusher
from .workers import Supervisor, sample_zone_ids

log = logging.getLogger(__name__)

CSRF_HEADER = "x-track-seeds"
ACCESS_COOKIE = "ts_access"
REFRESH_COOKIE = "ts_refresh"
MAX_IMAGE = 20 * 1024 * 1024
MAX_LIVE_VIEWERS = 8  # «Оригинал» — около 2 Мбит/с на зрителя; больше — не вытянет канал завода
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                 b"script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                 b"connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
                                 b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]
_HSTS = (b"strict-transport-security", b"max-age=15552000")


# --- пароли: API их не отдаёт; null в запросе — «не менять», "" — удалить ---

def masked(model: BaseModel, fields: tuple[str, ...] = ("password", "token")) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    for name in fields:
        if name in data:
            data[f"has_{name}"] = bool(getattr(model, name))
            data[name] = None
    if isinstance(model, CameraConfig):
        data["url"] = mask_url(model.url)
    return data


def merge[M: BaseModel](current: M | None, incoming: dict[str, Any], model: type[M],
                        secret_fields: tuple[str, ...] = ("password", "token")) -> M:
    """incoming поверх current. Секрет: null/нет поля — оставить прежний, "" — удалить, строка — заменить."""
    data = current.model_dump() if current is not None else {}
    for key, value in incoming.items():
        if key.startswith("has_"):
            continue
        if key in secret_fields:
            if value is None:
                continue
            value = value or None
        if key == "url" and isinstance(current, CameraConfig) and value == mask_url(current.url):
            continue  # вернулся замаскированный адрес — пароль внутри него не меняли
        data[key] = value
    return model.model_validate(data)


def _invalid(e: ValidationError) -> HTTPException:
    errors = [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]
    return HTTPException(422, "; ".join(errors))


def _jpeg(image: np.ndarray, quality: int = 90, max_width: int | None = None) -> bytes:
    if max_width and image.shape[1] > max_width:
        scale = max_width / image.shape[1]
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise HTTPException(500, "Не удалось закодировать кадр")
    return buf.tobytes()


async def _read_body(request: Request, limit: int) -> bytes:
    if int(request.headers.get("content-length") or 0) > limit:
        raise HTTPException(413, f"Файл больше {limit // (1024 * 1024)} МБ")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HTTPException(413, f"Файл больше {limit // (1024 * 1024)} МБ")
    if not body:
        raise HTTPException(422, "Пустой файл")
    return bytes(body)


_NO_CACHE = {"Cache-Control": "no-store"}


# --- запросы ---

class CrusherIn(BaseModel):
    """Что можно поменять у дробилки на месте; имя и зоны вшиты в программу."""

    enabled: bool = True
    notify: bool = True
    camera: dict[str, Any]
    seeds: SeedsConfig = SeedsConfig()
    speakers: list[dict[str, Any]] = []


class ProbeIn(BaseModel):
    camera: dict[str, Any]
    crusher_id: str | None = None  # взять сохранённый пароль камеры этой дробилки
    seconds: float = 3


class SampleIn(BaseModel):
    labels: dict[str, Literal["full", "empty"]]
    event_uid: str | None = None  # взять кадр из события, а не текущий


class LabelsIn(BaseModel):
    labels: dict[str, Literal["full", "empty"]]


class SettingsIn(BaseModel):
    notify: dict[str, Any] | None = None
    ntfy: dict[str, Any] | None = None
    voice: dict[str, Any] | None = None


class SayIn(BaseModel):
    text: str = ""


class LoginIn(BaseModel):
    username: str = Field(max_length=60)
    password: str = Field(max_length=200)
    code: str = Field("", max_length=12)


class SetupIn(BaseModel):
    username: str = Field(max_length=60)
    name: str = Field("", max_length=80)
    password: str = Field(max_length=200)


class RegisterIn(SetupIn):
    code: str = Field(max_length=100)


class PasswordIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


class CodeIn(BaseModel):
    code: str = Field(max_length=12)


class PasswordOnlyIn(BaseModel):
    password: str = Field(max_length=200)


class NameIn(BaseModel):
    name: str = Field("", max_length=60)


class InviteIn(BaseModel):
    note: str = Field("", max_length=120)
    hours: float = 24


class UserPatch(BaseModel):
    disabled: bool | None = None
    name: str | None = Field(None, max_length=80)


# --- защита до любого обработчика ---

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network
type Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def _networks(items: list[str] | None) -> list[Network] | None:
    return [ipaddress.ip_network(n, strict=False) for n in items] if items is not None else None


def _address(text: str | None) -> Address | None:
    try:
        address = ipaddress.ip_address((text or "").strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _within(address: Address | None, networks: list[Network] | None) -> bool:
    if networks is None:
        return True
    return address is not None and any(address in network for network in networks)


class _Security:
    """Адрес посетителя (за обратным прокси — из X-Forwarded-For), только из разрешённых сетей,
    защита от CSRF, защитные заголовки. Кто именно пришёл, решают зависимости маршрутов.

    Сайт открыт в интернет (any) — но напрямую на порт сервера пускают только из локальной сети:
    из интернета — через обратный прокси с HTTPS (Caddy), иначе пароль шёл бы открытым текстом,
    если порт 8080 случайно окажется доступен снаружи."""

    def __init__(self, app, networks: list[str] | None = None, trusted_proxies: list[str] | None = None,
                 admin_networks: list[str] | None = None, open_networks: list[str] | None = None) -> None:
        self._app = app
        self._networks = _networks(networks)
        if self._networks is not None and any(n.prefixlen == 0 for n in self._networks):
            self._networks = None  # any — откуда угодно
        self._proxies = _networks(trusted_proxies) or []
        self._admin = _networks(admin_networks)
        self._open = _networks(open_networks) or []
        self._local = _networks(LOCAL_NETWORKS)

    def _client(self, scope) -> tuple[Address | None, bool, bool]:
        """Адрес посетителя, пришёл ли он по HTTPS и через обратный прокси."""
        peer = _address((scope.get("client") or ("",))[0])
        https = scope.get("scheme") == "https"
        if peer is None or not _within(peer, self._proxies):
            return peer, https, False
        headers = scope["headers"]
        chain = [part.strip() for k, v in headers if k == b"x-forwarded-for" for part in v.decode("latin-1").split(",")]
        address = peer
        for item in reversed([c for c in chain if c]):
            address = _address(item)
            if address is None or not _within(address, self._proxies):
                break
        proto = next((v.decode("latin-1") for k, v in headers if k == b"x-forwarded-proto"), "")
        return address, https or proto.split(",")[0].strip().lower() == "https", bool(chain)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        address, https, proxied = self._client(scope)
        if not _within(address, self._networks):
            await self._reply(send, 403, "Доступ только из локальной сети")
            return
        if self._networks is None and not proxied and not _within(address, self._local):
            await self._reply(send, 403, "Из интернета сайт открывается только по HTTPS-адресу")
            return
        state = scope.setdefault("state", {})
        state["ip"] = str(address) if address else ""
        state["https"] = https
        state["admin_network"] = _within(address, self._admin)
        state["open_network"] = _within(address, self._open)
        state["local_network"] = _within(address, self._local)
        path, method = scope["path"], scope["method"]
        headers = dict(scope["headers"])
        if path.startswith("/api/") and method not in _SAFE_METHODS and headers.get(CSRF_HEADER.encode()) != b"1":
            await self._reply(send, 403, "Запрос без заголовка X-Track-Seeds отклонён")
            return
        extra = _HEADERS + ([_HSTS] if https else [])
        api = path.startswith("/api/")

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                current = list(message.get("headers", []))
                if api and not any(k.lower() == b"cache-control" for k, _ in current):
                    current.append((b"cache-control", b"no-store"))
                message["headers"] = current + extra
            await send(message)

        await self._app(scope, receive, send_with_headers)

    @staticmethod
    async def _reply(send, status: int, text: str) -> None:
        body = json.dumps({"detail": text}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                *_HEADERS]})
        await send({"type": "http.response.body", "body": body})


def create_app(sup: Supervisor, static_dir: Path | None = None, password: str | None = None,
               manage: bool = False, allowed_networks: list[str] | None = None, *,
               admin_networks: list[str] | None = None, trusted_proxies: list[str] | None = LOOPBACK,
               open_networks: list[str] | None = LOOPBACK, public_url: str = "",
               accounts: AccountStore | None = None) -> FastAPI:
    """allowed_networks — откуда пускать на сайт, admin_networks — откуда работает панель разработчика
    (None — откуда угодно), open_networks — откуда пускать без входа, пока разработчика не завели.
    password — WEB_PASSWORD: начальный пароль разработчика admin.
    manage — запускать и останавливать Supervisor вместе с сервером. Остановка идёт в lifespan:
    uvicorn после SIGTERM заново посылает сигнал процессу, и код после uvicorn.run() не выполняется."""

    accounts = accounts or AccountStore(sup.data_dir)
    if password and (done := accounts.bootstrap_admin(password)):
        log.info("Доступ: %s", done)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage:
            sup.start()
        try:
            yield
        finally:
            if manage:
                await asyncio.to_thread(sup.stop)  # дослать события из очереди, договорить фразу

    app = FastAPI(title="track-seeds", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.accounts = accounts
    store = sup.store
    data_dir = sup.data_dir
    public = APIRouter()
    viewer_api = APIRouter()
    widget_api = APIRouter()  # вошедшие или ключ виджета
    admin_api = APIRouter()

    @app.exception_handler(AuthError)
    async def auth_error(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(e), "code": e.code}, status_code=e.status)

    # --- кто пришёл ---

    def bearer(request: Request) -> str | None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return token.strip() or None if scheme.lower() == "bearer" else None

    def identify(request: Request) -> Principal | None:
        token = bearer(request)
        if token and not token.startswith(WIDGET_PREFIX):
            return accounts.authenticate(token)
        cookie = request.cookies.get(ACCESS_COOKIE)
        if cookie and (who := accounts.authenticate(cookie)):
            return who
        if request.state.open_network and not accounts.has_admin():
            return Principal(role="admin", name="Этот компьютер")
        return None

    def viewer(request: Request) -> Principal:
        who = identify(request)
        if who is None:
            raise HTTPException(401, "Войдите в систему")
        return who

    def admin(request: Request) -> Principal:
        who = viewer(request)
        if not who.admin:
            raise HTTPException(403, "Только для разработчика")
        if not request.state.admin_network:
            raise HTTPException(403, "Панель разработчика — только из локальной сети")
        return who

    def account(request: Request) -> Principal:
        """Вошедший под своим аккаунтом (а не «этот компьютер» без входа)."""
        who = viewer(request)
        if who.user_id is None:
            raise HTTPException(409, "Сначала заведите разработчика — логин и пароль")
        return who

    def widget_or_viewer(request: Request) -> Principal:
        token = bearer(request)
        if token and token.startswith(WIDGET_PREFIX):
            who = accounts.widget_principal(token)
            if who is None:
                raise HTTPException(401, "Ключ виджета отозван — создайте новый в «Мой аккаунт»")
            return who
        return viewer(request)

    def client_info(request: Request) -> tuple[str, str]:
        return request.state.ip, request.headers.get("user-agent", "")

    def me_out(who: Principal) -> dict[str, Any]:
        user = accounts.user(who.user_id) if who.user_id is not None else None
        return {"role": who.role, "user_id": who.user_id, "username": who.username,
                "name": (user or {}).get("name") or who.name or "Этот компьютер", "totp": bool(user and user["totp"]),
                "open": who.user_id is None, "session_id": who.session_id, "public_url": public_url}

    def signed_in(request: Request, user: dict, session_id: str, refresh: str | None) -> JSONResponse:
        who = Principal(role=user["role"], user_id=user["id"], session_id=session_id, username=user["username"],
                        name=user["name"])
        response = JSONResponse(me_out(who))
        secure = request.state.https
        response.set_cookie(ACCESS_COOKIE, accounts.access_token(user, session_id), max_age=ACCESS_TTL_S,
                            httponly=True, samesite="strict", secure=secure, path="/")
        if refresh:
            response.set_cookie(REFRESH_COOKIE, refresh, max_age=REFRESH_TTL_S[user["role"]], httponly=True,
                                samesite="strict", secure=secure, path="/api/auth")
        return response

    def signed_out(response: Response) -> Response:
        response.delete_cookie(ACCESS_COOKIE, path="/", httponly=True, samesite="strict")
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth", httponly=True, samesite="strict")
        return response

    # --- вход, регистрация по приглашению ---

    @public.get("/api/auth/info")
    def auth_info(request: Request) -> dict[str, Any]:
        """Для страницы входа: нужен ли вход вообще и можно ли отсюда войти разработчику."""
        has_admin = accounts.has_admin()
        return {"open_here": request.state.open_network and not has_admin, "has_admin": has_admin,
                "admin_here": request.state.admin_network, "public_url": public_url}

    @public.post("/api/auth/login")
    async def login(body: LoginIn, request: Request) -> Response:
        ip, agent = client_info(request)
        try:
            user, session_id, refresh = await asyncio.to_thread(
                accounts.login, body.username, body.password, body.code, ip, agent, request.state.admin_network)
        except AuthError as e:
            if e.status == 401 and not (e.code == "totp" and not body.code):
                await asyncio.sleep(1)  # перебор — не быстрее раза в секунду на соединение
            raise
        return signed_in(request, user, session_id, refresh)

    @public.post("/api/auth/refresh")
    def refresh(request: Request) -> Response:
        ip, agent = client_info(request)
        try:
            user, session_id, token = accounts.refresh(request.cookies.get(REFRESH_COOKIE, ""), ip, agent)
            if user["role"] == "admin" and not request.state.admin_network:
                raise AuthError("Панель разработчика — только из локальной сети", 403)
        except AuthError as e:
            return signed_out(JSONResponse({"detail": str(e), "code": e.code}, status_code=e.status))
        return signed_in(request, user, session_id, token)

    @public.post("/api/auth/logout")
    def logout(request: Request) -> Response:
        accounts.logout(request.cookies.get(REFRESH_COOKIE, ""))
        return signed_out(JSONResponse({"ok": True}))

    @public.get("/api/auth/invite")
    def invite_info(request: Request, code: str = Query(max_length=100)) -> dict[str, Any]:
        ip, _ = client_info(request)
        if wait := accounts.throttle.retry_after(ip):
            raise AuthError(f"Слишком много попыток — повторите через {max(1, round(wait / 60))} мин", 429)
        invite = accounts.invite(code)
        if invite is None:
            accounts.throttle.fail(ip)
            raise AuthError("Ссылка недействительна: срок истёк или по ней уже зарегистрировались", 410)
        return {"note": invite["note"], "expires": invite["expires"], "reset": invite["user_id"] is not None,
                "username": invite["for_user"]}

    @public.post("/api/auth/register")
    async def register(body: RegisterIn, request: Request) -> Response:
        ip, agent = client_info(request)
        user = await asyncio.to_thread(accounts.register, body.code, body.username, body.name, body.password, ip,
                                       agent)
        session_id, refresh_token = accounts.new_session(user["id"], user["role"], ip, agent)
        return signed_in(request, user, session_id, refresh_token)

    @public.post("/api/auth/setup")
    async def setup(body: SetupIn, request: Request) -> Response:
        """Завести разработчика с этого компьютера, пока входа нет (дальше — вход по паролю)."""
        who = identify(request)
        if who is None or who.user_id is not None or accounts.has_admin():
            raise HTTPException(409, "Разработчик уже заведён — войдите под ним")
        ip, agent = client_info(request)
        user = await asyncio.to_thread(accounts.create_user, body.username, body.name or "Разработчик",
                                       body.password, "admin")
        accounts.record("setup", True, user["id"], user["username"], ip, agent)
        session_id, refresh_token = accounts.new_session(user["id"], "admin", ip, agent)
        return signed_in(request, user, session_id, refresh_token)

    # --- свой аккаунт ---

    @viewer_api.get("/api/auth/me")
    def me(who: Principal = Depends(viewer)) -> dict[str, Any]:
        return me_out(who)

    @viewer_api.post("/api/auth/password")
    async def change_password(body: PasswordIn, request: Request, who: Principal = Depends(account)) -> dict:
        ip, agent = client_info(request)
        try:
            await asyncio.to_thread(accounts.check_current, who.user_id, body.current, ip)
        except AuthError:
            await asyncio.sleep(1)
            raise
        await asyncio.to_thread(accounts.set_password, who.user_id, body.new, who.session_id)
        accounts.record("password", True, who.user_id, who.username, ip, agent)
        return {"ok": True}

    @viewer_api.get("/api/auth/sessions")
    def sessions(who: Principal = Depends(account)) -> list[dict]:
        return [s | {"current": s["id"] == who.session_id} for s in accounts.sessions(who.user_id)]  # type: ignore[arg-type]

    @viewer_api.delete("/api/auth/sessions/{session_id}")
    def end_session(session_id: str, who: Principal = Depends(account)) -> dict:
        accounts.revoke_session(who.user_id, session_id)  # type: ignore[arg-type]
        return {"ok": True}

    @viewer_api.post("/api/auth/sessions/logout-others")
    def end_other_sessions(who: Principal = Depends(account)) -> dict:
        accounts.revoke_sessions(who.user_id, keep=who.session_id)  # type: ignore[arg-type]
        return {"ok": True}

    @viewer_api.post("/api/auth/totp/setup")
    def totp_setup(who: Principal = Depends(account)) -> dict:
        secret, uri = accounts.totp_begin(who.user_id)  # type: ignore[arg-type]
        return {"secret": secret, "uri": uri, "qr": qr_svg(uri)}

    @viewer_api.post("/api/auth/totp/enable")
    def totp_enable(body: CodeIn, who: Principal = Depends(account)) -> dict:
        accounts.totp_enable(who.user_id, body.code, keep_session=who.session_id)  # type: ignore[arg-type]
        return {"ok": True}

    @viewer_api.post("/api/auth/totp/disable")
    async def totp_disable(body: PasswordOnlyIn, request: Request, who: Principal = Depends(account)) -> dict:
        try:
            await asyncio.to_thread(accounts.totp_disable, who.user_id, body.password, request.state.ip)
        except AuthError:
            await asyncio.sleep(1)
            raise
        return {"ok": True}

    @viewer_api.get("/api/auth/widgets")
    def widgets(who: Principal = Depends(account)) -> list[dict]:
        return accounts.widgets(who.user_id)  # type: ignore[arg-type]

    @viewer_api.post("/api/auth/widgets")
    def create_widget(body: NameIn, request: Request, who: Principal = Depends(account)) -> dict:
        token, widget = accounts.create_widget(who.user_id, body.name)  # type: ignore[arg-type]
        accounts.record("widget", True, who.user_id, who.username, *client_info(request), widget["name"])
        return {"token": token, "widget": widget}

    @viewer_api.delete("/api/auth/widgets/{widget_id}")
    def revoke_widget(widget_id: int, who: Principal = Depends(account)) -> dict:
        accounts.revoke_widget(who.user_id, widget_id)  # type: ignore[arg-type]
        return {"ok": True}

    # --- клиенты и приглашения (разработчик) ---

    @admin_api.get("/api/admin/users")
    def users() -> list[dict]:
        return accounts.users()

    @admin_api.patch("/api/admin/users/{user_id}")
    def update_user(user_id: int, body: UserPatch, request: Request, who: Principal = Depends(admin)) -> dict:
        if user_id == who.user_id and body.disabled:
            raise HTTPException(409, "Себя не отключить")
        user = accounts.update_user(user_id, body.disabled, body.name)
        if body.disabled is not None:
            accounts.record("disable" if body.disabled else "enable", True, user_id, user["username"],
                            *client_info(request), f"разработчик {who.username or 'с этого компьютера'}")
        return user

    @admin_api.delete("/api/admin/users/{user_id}")
    def delete_user(user_id: int, request: Request, who: Principal = Depends(admin)) -> dict:
        if user_id == who.user_id:
            raise HTTPException(409, "Себя не удалить")
        user = accounts.user(user_id)
        accounts.delete_user(user_id)
        accounts.record("delete", True, user_id, user["username"] if user else None, *client_info(request))
        return {"ok": True}

    @admin_api.post("/api/admin/users/{user_id}/logout")
    def logout_user(user_id: int) -> dict:
        accounts.revoke_sessions(user_id)
        return {"ok": True}

    @admin_api.post("/api/admin/users/{user_id}/reset")
    def reset_link(user_id: int, body: InviteIn) -> dict:
        """Ссылка, по которой пользователь задаст новый пароль (старый продолжает работать до этого)."""
        code, invite = accounts.create_invite(body.note or "новый пароль", body.hours, user_id)
        return {"code": code, "invite": invite}

    @admin_api.get("/api/admin/invites")
    def invites() -> dict:
        return {"invites": accounts.invites(), "hours": INVITE_HOURS}

    @admin_api.post("/api/admin/invites")
    def create_invite(body: InviteIn, request: Request, who: Principal = Depends(admin)) -> dict:
        code, invite = accounts.create_invite(body.note, body.hours)
        accounts.record("invite", True, who.user_id, who.username, *client_info(request), body.note or None)
        return {"code": code, "invite": invite}

    @admin_api.delete("/api/admin/invites/{invite_id}")
    def revoke_invite(invite_id: int) -> dict:
        accounts.revoke_invite(invite_id)
        return {"ok": True}

    @admin_api.get("/api/admin/log")
    def auth_log(limit: int = 100) -> list[dict]:
        return accounts.journal(limit)

    # --- дробилки: общее ---

    def crusher_or_404(crusher_id: str) -> Crusher:
        crusher = store.crusher(crusher_id)
        if crusher is None:
            raise HTTPException(404, "Дробилка не найдена")
        return crusher

    def crusher_status(crusher: Crusher, history: bool) -> dict[str, Any]:
        worker = sup.worker(crusher.id)
        status = worker.status() if worker else {"state": "stopped"}
        if not history and status.get("seeds"):
            for zone in status["seeds"]["zones"]:
                zone.pop("history", None)
        return status

    def crusher_out(crusher: Crusher, history: bool = True) -> dict[str, Any]:
        data = crusher.model_dump(mode="json")
        data["camera"] = masked(crusher.camera)
        data["speakers"] = [masked(s) for s in crusher.speakers]
        data["folder"] = str(store.crusher_dir(crusher.id))
        data["builtin_camera_url"] = store.specs[crusher.id].camera_url
        data["config_error"] = store.errors.get(crusher.id)
        data["status"] = crusher_status(crusher, history)
        return data

    def crusher_view(crusher: Crusher, history: bool = True) -> dict[str, Any]:
        """Для клиента: состояние и зоны — без адресов камер, паролей, динамиков и текстов ошибок."""
        status = crusher_status(crusher, history)
        view = {k: status.get(k) for k in ("state", "connected", "since", "fps", "width", "height", "last_frame_ts",
                                            "seeds")}
        view["error"] = "Камера не подключена" if status.get("state") == "error" else None
        return {"id": crusher.id, "name": crusher.name, "enabled": crusher.enabled,
                "seeds": {"zones": [z.model_dump(mode="json") for z in crusher.seeds.zones],
                          "motion_threshold": crusher.seeds.motion_threshold},
                "status": view}

    def event_view(event: dict) -> dict:
        return event | {"deliveries": [], "data": {k: event["data"].get(k) for k in ("level", "previous")}}

    def build_crusher(body: CrusherIn, current: Crusher) -> Crusher:
        try:
            camera = merge(current.camera, body.camera, CameraConfig)
            existing = {s.id: s for s in current.speakers}
            speakers = []
            for raw in body.speakers:
                speaker_id = str(raw.get("id") or "") or secrets.token_hex(4)
                speakers.append(merge(existing.get(speaker_id), {**raw, "id": speaker_id}, SpeakerConfig))
        except ValidationError as e:
            raise _invalid(e) from e
        if not camera.url.strip():
            raise HTTPException(422, "Укажите адрес камеры")
        return Crusher(id=current.id, name=current.name, enabled=body.enabled, notify=body.notify, camera=camera,
                       seeds=body.seeds, speakers=speakers)

    @public.get("/api/health")
    def health(request: Request) -> Response:
        """Для HEALTHCHECK в Docker и мониторинга; без входа. Подробности — только из локальной сети
        (не из сетей разработчика: их можно открыть в интернет, а здесь входа нет)."""
        alive = sup.bus.alive and sup.announcer.alive
        body: dict[str, Any] = {"ok": alive}
        if request.state.local_network:
            body |= {"event_bus": sup.bus.alive, "announcer": sup.announcer.alive,
                     "crushers": {c.name: (w.state if (w := sup.worker(c.id)) else "stopped") for c in store.ordered()},
                     "config_errors": sorted(store.errors)}
        return Response(json.dumps(body, ensure_ascii=False), status_code=200 if alive else 503,
                        media_type="application/json")

    @admin_api.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "schemas": {"seeds": SeedsConfig.model_json_schema(), "voice": VoiceConfig.model_json_schema(),
                        "notify": NotifyConfig.model_json_schema()},
            "voices": VOICES,
            "engines": {"piper": piper_installed(), "espeak": espeak_installed()},
            "channels": list(CHANNELS),
            "auth": accounts.has_admin(),
            "config_dir": str(store.root.resolve()),
            "public_url": public_url,
        }

    @viewer_api.get("/api/overview")
    def overview(who: Principal = Depends(viewer)) -> dict[str, Any]:
        if not who.admin:
            return {"crushers": [crusher_view(c, history=False) for c in store.ordered()],
                    "events": [event_view(e) for e in sup.events.query(limit=15, tests=False)],
                    "last_seq": sup.events.last_seq(), "time": time.time()}
        return {"crushers": [crusher_out(c, history=False) for c in store.ordered()],
                "errors": store.errors,
                "events": sup.events.query(limit=15), "last_seq": sup.events.last_seq(),
                "notify_error": sup.notify_error, "time": time.time()}

    @viewer_api.get("/api/crushers/{crusher_id}")
    def get(crusher_id: str, history: bool = True, who: Principal = Depends(viewer)) -> dict[str, Any]:
        crusher = crusher_or_404(crusher_id)
        return crusher_out(crusher, history) if who.admin else crusher_view(crusher, history)

    @viewer_api.get("/api/crushers/{crusher_id}/frame.jpg")
    def frame(crusher_id: str, w: int | None = Query(None, ge=160, le=4096)) -> Response:
        """Текущий кадр без разметки (зоны рисует браузер); w — уменьшить до такой ширины."""
        crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        image = worker.raw_frame() if worker else None
        if image is None:
            raise HTTPException(503, "Нет кадра: камера не подключена")
        return Response(_jpeg(image, 85 if w else 90, w), media_type="image/jpeg", headers=_NO_CACHE)

    live_viewers = [0]

    @viewer_api.get("/api/crushers/{crusher_id}/live.mjpg")
    async def live(crusher_id: str, who: Principal = Depends(viewer)) -> StreamingResponse:
        """«Оригинал»: видео с камеры как есть (MJPEG до 12 к/с). Отозвали доступ — поток обрывается."""
        crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        if worker is None:
            raise HTTPException(503, "Камера выключена")
        if live_viewers[0] >= MAX_LIVE_VIEWERS:
            raise HTTPException(429, "Сейчас смотрят слишком многие — включите «Зоны» или попробуйте позже")

        def allowed() -> bool:
            if who.session_id is not None:
                return accounts.session_alive(who.session_id)
            return not accounts.has_admin()  # без входа с этого компьютера — пока разработчика не завели

        async def frames():
            live_viewers[0] += 1
            try:
                seq, checked = -1, time.monotonic()
                while True:
                    if time.monotonic() - checked >= 10:
                        checked = time.monotonic()
                        if not await asyncio.to_thread(allowed):
                            break
                    current, jpeg = await asyncio.to_thread(worker.live_jpeg)
                    if jpeg is not None and current != seq:
                        seq = current
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n"
                               % (len(jpeg), jpeg))
                    await asyncio.sleep(0.04)
            finally:
                live_viewers[0] -= 1

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame", headers=_NO_CACHE)

    # --- дробилки: настройки (разработчик) ---

    @admin_api.put("/api/crushers/{crusher_id}")
    def update(crusher_id: str, body: CrusherIn) -> dict[str, Any]:
        return crusher_out(sup.update_crusher(build_crusher(body, crusher_or_404(crusher_id))))

    @admin_api.post("/api/crushers/probe")
    def probe(body: ProbeIn) -> dict[str, Any]:
        current = store.crusher(body.crusher_id) if body.crusher_id else None
        try:
            settings = merge(current.camera if current else None, body.camera, CameraConfig)
        except ValidationError as e:
            raise _invalid(e) from e
        result = probe_camera(settings, min(max(body.seconds, 1), 10))
        return {
            "ok": result.ok, "error": result.error, "connect_s": round(result.connect_s, 2),
            "width": result.width, "height": result.height, "codec": result.codec,
            "measured_fps": round(result.measured_fps, 1), "read_failures": result.read_failures,
            "image": base64.b64encode(_jpeg(result.image, 85, 1280)).decode() if result.image is not None else None,
        }

    @admin_api.get("/api/crushers/{crusher_id}/snapshot.jpg")
    def snapshot(crusher_id: str) -> Response:
        """Кадр с разметкой, нарисованной сервером."""
        crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        if worker is None:
            raise HTTPException(503, "Камера выключена")
        worker.hub.touch()
        _, jpeg, ts = worker.hub.latest()
        if jpeg is None or time.time() - ts > 3:
            image = worker.raw_frame()
            if image is not None:
                jpeg = _jpeg(image, 80, 1280)
        if jpeg is None:
            raise HTTPException(503, "Нет кадра: камера не подключена")
        return Response(jpeg, media_type="image/jpeg", headers=_NO_CACHE)

    @admin_api.get("/api/crushers/{crusher_id}/stream.mjpg")
    async def stream(crusher_id: str) -> StreamingResponse:
        crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        if worker is None:
            raise HTTPException(503, "Камера выключена")
        hub = worker.hub

        async def frames():
            hub.open_stream()
            try:
                seq = -1
                while True:
                    current, jpeg, _ = hub.latest()
                    if jpeg is not None and current != seq:
                        seq = current
                        yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n"
                               % (len(jpeg), jpeg))
                    await asyncio.sleep(0.05)
            finally:
                hub.close_stream()

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame", headers=_NO_CACHE)

    @admin_api.post("/api/crushers/{crusher_id}/test-alarm")
    def test_alarm(crusher_id: str) -> dict[str, Any]:
        """Проверка всей цепочки: журнал, уведомления, динамики, звук в браузере — как при «закончились»."""
        crusher = crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        image = worker.raw_frame() if worker else None
        event = SeedsLevelChanged(camera=crusher.name, camera_id=crusher_id, level="empty", previous="ok",
                                  duration_s=0, zones=tuple(z.name for z in crusher.seeds.zones if z.kind == "empty"),
                                  test=True, snapshot=_jpeg(image, 85) if image is not None else None)
        sup.bus.publish(event)
        return {"ok": True, "uid": event.id}

    # --- эталоны ---

    @contextmanager
    def sample_errors():
        try:
            yield
        except KeyError as e:
            raise HTTPException(404, "Эталон не найден") from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    @admin_api.post("/api/crushers/{crusher_id}/samples")
    def add_sample(crusher_id: str, body: SampleIn) -> dict[str, Any]:
        crusher_or_404(crusher_id)
        image = None
        if body.event_uid:
            event = sup.events.get(body.event_uid)
            if event is None or not event["frame"]:
                raise HTTPException(404, "У события нет кадра")
            image = cv2.imread(str(data_dir / event["frame"]))
            if image is None:
                raise HTTPException(404, "Кадр события удалён")
        try:
            return sup.add_sample(crusher_id, dict(body.labels), image).model_dump(mode="json")
        except (ValueError, RuntimeError) as e:
            raise HTTPException(422, str(e)) from e

    @admin_api.post("/api/crushers/{crusher_id}/samples/upload")
    async def upload_sample(crusher_id: str, request: Request, label: Literal["full", "empty"],
                            zones: str = "") -> dict[str, Any]:
        """Эталон из файла — например, кадр пустого бункера из архива регистратора (та же камера, тот же ракурс)."""
        crusher = crusher_or_404(crusher_id)
        data = await _read_body(request, MAX_IMAGE)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(422, "Это не картинка (нужен JPEG или PNG)")
        worker = sup.worker(crusher_id)
        live_image = worker.raw_frame() if worker else None
        if live_image is not None:
            aspect, live_aspect = image.shape[1] / image.shape[0], live_image.shape[1] / live_image.shape[0]
            if abs(aspect - live_aspect) > 0.03 * live_aspect:
                raise HTTPException(422, f"Пропорции кадра {image.shape[1]}×{image.shape[0]} не совпадают с камерой "
                                         f"{live_image.shape[1]}×{live_image.shape[0]} — нужен кадр с этой же камеры")
        wanted = {z for z in zones.split(",") if z} or sample_zone_ids(crusher)
        try:
            sample = await asyncio.to_thread(sup.add_sample, crusher_id, {z: label for z in wanted}, image)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(422, str(e)) from e
        return sample.model_dump(mode="json")

    @admin_api.get("/api/crushers/{crusher_id}/samples/{sample_id}.jpg")
    def sample_image(crusher_id: str, sample_id: str) -> FileResponse:
        crusher = crusher_or_404(crusher_id)
        sample = next((s for s in crusher.seeds.samples if s.id == sample_id), None)
        path = store.sample_path(crusher_id, sample) if sample else None
        if path is None or not path.is_file():
            raise HTTPException(404, "Эталон не найден")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @admin_api.patch("/api/crushers/{crusher_id}/samples/{sample_id}")
    def update_sample(crusher_id: str, sample_id: str, body: LabelsIn) -> dict[str, bool]:
        crusher_or_404(crusher_id)
        with sample_errors():
            sup.update_sample(crusher_id, sample_id, dict(body.labels))
        return {"ok": True}

    @admin_api.delete("/api/crushers/{crusher_id}/samples/{sample_id}")
    def remove_sample(crusher_id: str, sample_id: str) -> dict[str, bool]:
        crusher_or_404(crusher_id)
        with sample_errors():
            sup.remove_sample(crusher_id, sample_id)
        return {"ok": True}

    # --- динамики дробилки ---

    def speaker_or_404(crusher_id: str, speaker_id: str):
        crusher = crusher_or_404(crusher_id)
        index = next((i for i, s in enumerate(crusher.speakers) if s.id == speaker_id), None)
        if index is None:
            raise HTTPException(404, "Динамик не найден — сохраните его сначала")
        return sup.speakers(crusher_id)[index]

    @admin_api.post("/api/crushers/{crusher_id}/speakers/{speaker_id}/test")
    def test_speaker(crusher_id: str, speaker_id: str, body: SayIn) -> dict[str, Any]:
        speaker = speaker_or_404(crusher_id, speaker_id)
        text = body.text.strip() or "Проверка связи. Это тестовое оповещение."
        error = sup.announcer.say(text, [speaker]).get(speaker.name)
        return {"ok": error is None, "error": error}

    @admin_api.post("/api/crushers/{crusher_id}/speakers/{speaker_id}/probe")
    def probe_speaker(crusher_id: str, speaker_id: str) -> dict[str, Any]:
        speaker = speaker_or_404(crusher_id, speaker_id)
        try:
            return {"ok": True, "info": speaker.probe()}  # type: ignore[attr-defined]
        except SpeakerError as e:
            return {"ok": False, "error": str(e)}

    # --- журнал событий ---

    @viewer_api.get("/api/events")
    def events(camera_id: str | None = None, after: int | None = None, before: int | None = None,
               type: list[str] | None = Query(None), kind: list[str] | None = Query(None), limit: int = 100,
               who: Principal = Depends(viewer)) -> list[dict[str, Any]]:
        """kind — виды событий (eventlog.KINDS): empty, stopped, started…; несколько — любой из них."""
        if who.admin:
            return sup.events.query(camera_id, after, before, type, limit, kinds=kind)
        return [event_view(e) for e in sup.events.query(camera_id, after, before, type, limit, tests=False, kinds=kind)]

    @viewer_api.get("/api/events/kinds")
    def event_kinds(camera_id: str | None = None, who: Principal = Depends(viewer)) -> dict[str, int]:
        """Сколько событий каждого вида — числа на кнопках фильтра."""
        return sup.events.counts(camera_id, tests=who.admin)

    @viewer_api.get("/api/files/{path:path}")
    def files(path: str) -> FileResponse:
        target = (data_dir / path).resolve()
        if not target.is_relative_to((data_dir / "events").resolve()) or not target.is_file():
            raise HTTPException(404, "Файл не найден")
        return FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})

    def speech(text: str) -> Response:
        try:
            synth = sup.synthesizer()
            audio, rate = synth.synthesize(text)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:
            raise HTTPException(503, f"Синтез речи недоступен: {e}") from e
        audio, audio_rate = compose(audio, rate, sup.settings.voice, 1)
        return Response(wav_bytes(audio, audio_rate), media_type="audio/wav",
                        headers=_NO_CACHE | {"X-Voice": synth.voice, "X-Voice-Engine": synth.engine})

    @viewer_api.get("/api/events/{uid}/speech.wav")
    def event_speech(uid: str) -> Response:
        """Фраза тревоги — для звука в браузере (клиенту — только фразы из журнала, свой текст не синтезировать)."""
        event = sup.events.get(uid)
        if event is None or not event["speech"]:
            raise HTTPException(404, "У события нет фразы")
        return speech(event["speech"])

    # --- отчёты и виджет ---

    @viewer_api.get("/api/reports")
    def reports(period: Literal["day", "week", "month"] = "day",
                day: str | None = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict[str, Any]:
        try:
            when = date.fromisoformat(day) if day else datetime.now().date()
        except ValueError as e:
            raise HTTPException(422, "Дата — ГГГГ-ММ-ДД") from e
        return worklog.report(sup.worklog, [(c.id, c.name) for c in store.ordered()], period, when, time.time())

    labels = {"running": "Работает", "idle": "Без зерна", "stopped": "Стоит", "offline": "Нет связи",
              "unknown": "Определяется", None: "Не наблюдается"}

    @widget_api.get("/api/widget")
    def widget() -> dict[str, Any]:
        """Сводка для виджета на телефоне: состояние каждой дробилки и итоги дня."""
        now = time.time()
        items = []
        for crusher in store.ordered():
            worker = sup.worker(crusher.id)
            state = worker.work_state() if worker else None
            status = worker.status() if worker else {}
            seeds = status.get("seeds") or {}
            since = {"offline": status.get("since"), "idle": seeds.get("level_since"),
                     None: None}.get(state, seeds.get("running_since"))
            items.append({"id": crusher.id, "name": crusher.name, "state": state, "label": labels[state],
                          "since": since, "today": worklog.today(sup.worklog, crusher.id, now)})
        return {"time": now, "crushers": items, "url": public_url}

    @widget_api.get("/api/widget/{crusher_id}.jpg")
    def widget_image(crusher_id: str, w: int = Query(480, ge=160, le=960)) -> Response:
        """Кадр для виджета; w — ширина в пикселях (виджет просит по размеру плитки: iPad — крупнее)."""
        crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        image = worker.raw_frame() if worker else None
        if image is None:
            raise HTTPException(503, "Нет кадра: камера не подключена")
        return Response(_jpeg(image, 70, w), media_type="image/jpeg", headers=_NO_CACHE)

    # --- общие настройки ---

    def settings_out() -> dict[str, Any]:
        s = sup.settings
        return {"notify": s.notify.model_dump(mode="json"), "ntfy": masked(s.ntfy),
                "voice": s.voice.model_dump(mode="json"), "notify_error": sup.notify_error}

    @admin_api.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return settings_out()

    @admin_api.put("/api/settings")
    def put_settings(body: SettingsIn) -> dict[str, Any]:
        s = sup.settings
        try:
            notify = merge(s.notify, body.notify, NotifyConfig) if body.notify else None
            ntfy = merge(s.ntfy, body.ntfy, NtfyConfig) if body.ntfy else None
            voice = merge(s.voice, body.voice, VoiceConfig) if body.voice else None
        except ValidationError as e:
            raise _invalid(e) from e
        if notify is not None and (unknown := set(notify.channels) - CHANNELS.keys()):
            raise HTTPException(422, f"Неизвестные каналы: {', '.join(sorted(unknown))}")
        if voice is not None and voice.voice not in VOICES:
            raise HTTPException(422, f"Неизвестный голос: {voice.voice}")

        def apply(c) -> None:
            for key, value in (("notify", notify), ("ntfy", ntfy), ("voice", voice)):
                if value is not None:
                    setattr(c, key, value)

        sup.update_settings(apply)
        return settings_out()

    @admin_api.post("/api/notify/test")
    def notify_test() -> dict[str, Any]:
        notification = Notification(title="track-seeds: тестовое уведомление",
                                    message=f"Уведомления работают — {time.strftime('%H:%M:%S')}",
                                    tags=("test_tube",))
        return {"results": sup.test_notify(notification), "notify_error": sup.notify_error}

    @admin_api.get("/api/voice/say.wav")
    def say_wav(text: str = Query(..., max_length=500)) -> Response:
        """Фраза с сигналом — для проигрывания в браузере. Без кеша браузера: после смены голоса
        тот же текст должен звучать новым голосом (на сервере фразы кешируются с учётом голоса)."""
        return speech(text)

    @admin_api.get("/api/voice/status")
    def voice_status() -> dict[str, Any]:
        return sup.voice_status()

    # --- маршруты: без входа, для вошедших, для разработчика ---

    app.include_router(public)
    app.include_router(viewer_api, dependencies=[Depends(viewer)])
    app.include_router(widget_api, dependencies=[Depends(widget_or_viewer)])
    app.include_router(admin_api, dependencies=[Depends(admin)])

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def not_found(path: str) -> None:
        raise HTTPException(404, "Нет такого адреса API")

    # --- фронтенд ---

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_frontend() -> str:
            return ("<meta charset=utf-8><h1>track-seeds</h1><p>API работает, но веб-интерфейс не собран: "
                    "<code>make web</code>.</p>")

    app.add_middleware(_Security, networks=allowed_networks, trusted_proxies=trusted_proxies,
                       admin_networks=admin_networks, open_networks=open_networks)
    return app
