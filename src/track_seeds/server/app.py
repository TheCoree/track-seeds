"""HTTP API веб-интерфейса и раздача собранного фронтенда (Next.js, статический экспорт).

Защита: пароль на вход (WEB_PASSWORD, обязателен при доступе из сети), изменяющие запросы
только с заголовком X-Track-Seeds (чужая страница в браузере не сможет их отправить — CSRF),
пауза после неверного пароля, защитные заголовки, ограничения на размер загрузок,
пароли камер и динамиков наружу не отдаются.
"""

import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from ..camera import mask_url, probe_camera
from ..config import CameraConfig, NotifyConfig, NtfyConfig, SeedsConfig, SpeakerConfig, VoiceConfig
from ..events import SeedsLevelChanged
from ..notify import CHANNELS, Notification
from ..voice import VOICES, SpeakerError, compose, espeak_installed, piper_installed
from ..voice.audio import wav_bytes
from .store import Crusher
from .workers import Supervisor, sample_zone_ids

log = logging.getLogger(__name__)

CSRF_HEADER = "x-track-seeds"
MAX_IMAGE = 20 * 1024 * 1024
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                 b"script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                 b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
]


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


class _Security:
    """Только из разрешённых сетей, пароль, защита от CSRF и защитные заголовки — до любого обработчика."""

    def __init__(self, app, password: str | None, networks: list[str] | None = None) -> None:
        self._app = app
        self._password = password.encode() if password else None
        self._networks = [ipaddress.ip_network(n, strict=False) for n in networks] if networks is not None else None

    def _from_allowed_network(self, client) -> bool:
        if self._networks is None:
            return True
        try:
            address = ipaddress.ip_address(client[0])
        except (TypeError, ValueError, IndexError):
            return False  # адрес не определить — не пускать
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in self._networks)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if not self._from_allowed_network(scope.get("client")):
            await self._reply(send, 403, "Доступ только из локальной сети")
            return
        path, method = scope["path"], scope["method"]
        headers = dict(scope["headers"])
        if self._password and path != "/api/health" and not self._allowed(headers.get(b"authorization", b"")):
            await asyncio.sleep(1)  # перебор паролей — не быстрее раза в секунду на соединение
            await self._reply(send, 401, "Нужен пароль", [(b"www-authenticate", b'Basic realm="track-seeds"')])
            return
        if path.startswith("/api/") and method not in _SAFE_METHODS and headers.get(CSRF_HEADER.encode()) != b"1":
            await self._reply(send, 403, "Запрос без заголовка X-Track-Seeds отклонён")
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + _HEADERS
            await send(message)

        await self._app(scope, receive, send_with_headers)

    def _allowed(self, header: bytes) -> bool:
        if not header.lower().startswith(b"basic "):
            return False
        try:
            _, _, password = base64.b64decode(header[6:]).partition(b":")
        except (binascii.Error, ValueError):
            return False
        assert self._password is not None
        return secrets.compare_digest(password, self._password)

    @staticmethod
    async def _reply(send, status: int, text: str, extra: list | None = None) -> None:
        body = json.dumps({"detail": text}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                *_HEADERS, *(extra or [])]})
        await send({"type": "http.response.body", "body": body})


def create_app(sup: Supervisor, static_dir: Path | None = None, password: str | None = None,
               manage: bool = False, allowed_networks: list[str] | None = None) -> FastAPI:
    """allowed_networks — откуда пускать (None — откуда угодно).
    manage — запускать и останавливать Supervisor вместе с сервером. Остановка идёт в lifespan:
    uvicorn после SIGTERM заново посылает сигнал процессу, и код после uvicorn.run() не выполняется."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage:
            sup.start()
        try:
            yield
        finally:
            if manage:
                await asyncio.to_thread(sup.stop)  # дослать события из очереди, договорить фразу

    app = FastAPI(title="track-seeds", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    store = sup.store
    data_dir = sup.data_dir

    def crusher_or_404(crusher_id: str) -> Crusher:
        crusher = store.crusher(crusher_id)
        if crusher is None:
            raise HTTPException(404, "Дробилка не найдена")
        return crusher

    def crusher_out(crusher: Crusher, history: bool = True) -> dict[str, Any]:
        data = crusher.model_dump(mode="json")
        data["camera"] = masked(crusher.camera)
        data["speakers"] = [masked(s) for s in crusher.speakers]
        data["folder"] = str(store.crusher_dir(crusher.id))
        data["builtin_camera_url"] = store.specs[crusher.id].camera_url
        data["config_error"] = store.errors.get(crusher.id)
        worker = sup.worker(crusher.id)
        status = worker.status() if worker else {"state": "stopped"}
        if not history and status.get("seeds"):
            for zone in status["seeds"]["zones"]:
                zone.pop("history", None)
        data["status"] = status
        return data

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

    # --- общее ---

    @app.get("/api/health")
    def health() -> Response:
        """Для HEALTHCHECK в Docker и внешнего мониторинга; без пароля."""
        alive = sup.bus.alive and sup.announcer.alive
        crushers = {c.name: (w.state if (w := sup.worker(c.id)) else "stopped") for c in store.ordered()}
        body = {"ok": alive, "event_bus": sup.bus.alive, "announcer": sup.announcer.alive, "crushers": crushers,
                "config_errors": sorted(store.errors)}
        return Response(json.dumps(body, ensure_ascii=False), status_code=200 if alive else 503,
                        media_type="application/json")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "schemas": {"seeds": SeedsConfig.model_json_schema(), "voice": VoiceConfig.model_json_schema(),
                        "notify": NotifyConfig.model_json_schema()},
            "voices": VOICES,
            "engines": {"piper": piper_installed(), "espeak": espeak_installed()},
            "channels": list(CHANNELS),
            "auth": bool(password),
            "config_dir": str(store.root.resolve()),
        }

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        return {"crushers": [crusher_out(c, history=False) for c in store.ordered()],
                "errors": store.errors,
                "events": sup.events.query(limit=15), "last_seq": sup.events.last_seq(),
                "notify_error": sup.notify_error, "time": time.time()}

    # --- дробилки ---

    @app.get("/api/crushers/{crusher_id}")
    def get(crusher_id: str, history: bool = True) -> dict[str, Any]:
        return crusher_out(crusher_or_404(crusher_id), history)

    @app.put("/api/crushers/{crusher_id}")
    def update(crusher_id: str, body: CrusherIn) -> dict[str, Any]:
        return crusher_out(sup.update_crusher(build_crusher(body, crusher_or_404(crusher_id))))

    @app.post("/api/crushers/probe")
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

    @app.get("/api/crushers/{crusher_id}/frame.jpg")
    def frame(crusher_id: str, w: int | None = Query(None, ge=160, le=4096)) -> Response:
        """Текущий кадр без разметки (зоны рисует браузер); w — уменьшить до такой ширины."""
        crusher_or_404(crusher_id)
        worker = sup.worker(crusher_id)
        image = worker.raw_frame() if worker else None
        if image is None:
            raise HTTPException(503, "Нет кадра: камера не подключена")
        return Response(_jpeg(image, 85 if w else 90, w), media_type="image/jpeg", headers=_NO_CACHE)

    @app.get("/api/crushers/{crusher_id}/snapshot.jpg")
    def snapshot(crusher_id: str) -> Response:
        """Кадр с разметкой зон (плитки на главной)."""
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

    @app.get("/api/crushers/{crusher_id}/stream.mjpg")
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

    @app.post("/api/crushers/{crusher_id}/test-alarm")
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

    @app.post("/api/crushers/{crusher_id}/samples")
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

    @app.post("/api/crushers/{crusher_id}/samples/upload")
    async def upload_sample(crusher_id: str, request: Request, label: Literal["full", "empty"],
                            zones: str = "") -> dict[str, Any]:
        """Эталон из файла — например, кадр пустого бункера из архива регистратора (та же камера, тот же ракурс)."""
        crusher = crusher_or_404(crusher_id)
        data = await _read_body(request, MAX_IMAGE)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(422, "Это не картинка (нужен JPEG или PNG)")
        worker = sup.worker(crusher_id)
        live = worker.raw_frame() if worker else None
        if live is not None:
            aspect, live_aspect = image.shape[1] / image.shape[0], live.shape[1] / live.shape[0]
            if abs(aspect - live_aspect) > 0.03 * live_aspect:
                raise HTTPException(422, f"Пропорции кадра {image.shape[1]}×{image.shape[0]} не совпадают с камерой "
                                         f"{live.shape[1]}×{live.shape[0]} — нужен кадр с этой же камеры")
        wanted = {z for z in zones.split(",") if z} or sample_zone_ids(crusher)
        try:
            sample = await asyncio.to_thread(sup.add_sample, crusher_id, {z: label for z in wanted}, image)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(422, str(e)) from e
        return sample.model_dump(mode="json")

    @app.get("/api/crushers/{crusher_id}/samples/{sample_id}.jpg")
    def sample_image(crusher_id: str, sample_id: str) -> FileResponse:
        crusher = crusher_or_404(crusher_id)
        sample = next((s for s in crusher.seeds.samples if s.id == sample_id), None)
        path = store.sample_path(crusher_id, sample) if sample else None
        if path is None or not path.is_file():
            raise HTTPException(404, "Эталон не найден")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.patch("/api/crushers/{crusher_id}/samples/{sample_id}")
    def update_sample(crusher_id: str, sample_id: str, body: LabelsIn) -> dict[str, bool]:
        crusher_or_404(crusher_id)
        with sample_errors():
            sup.update_sample(crusher_id, sample_id, dict(body.labels))
        return {"ok": True}

    @app.delete("/api/crushers/{crusher_id}/samples/{sample_id}")
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

    @app.post("/api/crushers/{crusher_id}/speakers/{speaker_id}/test")
    def test_speaker(crusher_id: str, speaker_id: str, body: SayIn) -> dict[str, Any]:
        speaker = speaker_or_404(crusher_id, speaker_id)
        text = body.text.strip() or "Проверка связи. Это тестовое оповещение."
        error = sup.announcer.say(text, [speaker]).get(speaker.name)
        return {"ok": error is None, "error": error}

    @app.post("/api/crushers/{crusher_id}/speakers/{speaker_id}/probe")
    def probe_speaker(crusher_id: str, speaker_id: str) -> dict[str, Any]:
        speaker = speaker_or_404(crusher_id, speaker_id)
        try:
            return {"ok": True, "info": speaker.probe()}  # type: ignore[attr-defined]
        except SpeakerError as e:
            return {"ok": False, "error": str(e)}

    # --- журнал ---

    @app.get("/api/events")
    def events(camera_id: str | None = None, after: int | None = None, before: int | None = None,
               type: list[str] | None = Query(None), limit: int = 100) -> list[dict[str, Any]]:
        return sup.events.query(camera_id, after, before, type, limit)

    @app.get("/api/files/{path:path}")
    def files(path: str) -> FileResponse:
        target = (data_dir / path).resolve()
        if not target.is_relative_to((data_dir / "events").resolve()) or not target.is_file():
            raise HTTPException(404, "Файл не найден")
        return FileResponse(target, headers={"Cache-Control": "private, max-age=86400"})

    # --- общие настройки ---

    def settings_out() -> dict[str, Any]:
        s = sup.settings
        return {"notify": s.notify.model_dump(mode="json"), "ntfy": masked(s.ntfy),
                "voice": s.voice.model_dump(mode="json"), "notify_error": sup.notify_error}

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return settings_out()

    @app.put("/api/settings")
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

    @app.post("/api/notify/test")
    def notify_test() -> dict[str, Any]:
        notification = Notification(title="track-seeds: тестовое уведомление",
                                    message=f"Уведомления работают — {time.strftime('%H:%M:%S')}",
                                    tags=("test_tube",))
        return {"results": sup.test_notify(notification), "notify_error": sup.notify_error}

    @app.get("/api/voice/say.wav")
    def say_wav(text: str = Query(..., max_length=500)) -> Response:
        """Фраза с сигналом — для проигрывания в браузере. Без кеша браузера: после смены голоса
        тот же текст должен звучать новым голосом (на сервере фразы кешируются с учётом голоса)."""
        try:
            synth = sup.synthesizer()
            speech, rate = synth.synthesize(text)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except Exception as e:
            raise HTTPException(503, f"Синтез речи недоступен: {e}") from e
        audio, audio_rate = compose(speech, rate, sup.settings.voice, 1)
        return Response(wav_bytes(audio, audio_rate), media_type="audio/wav",
                        headers=_NO_CACHE | {"X-Voice": synth.voice, "X-Voice-Engine": synth.engine})

    @app.get("/api/voice/status")
    def voice_status() -> dict[str, Any]:
        return sup.voice_status()

    # --- фронтенд ---

    if static_dir is not None and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_frontend() -> str:
            return ("<meta charset=utf-8><h1>track-seeds</h1><p>API работает, но веб-интерфейс не собран: "
                    "<code>make web</code>.</p>")

    app.add_middleware(_Security, password=password, networks=allowed_networks)
    return app
