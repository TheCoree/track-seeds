"""Динамики: IP-камера Hikvision (ISAPI, двусторонний звук) и звуковая карта сервера."""

import base64
import hashlib
import http.client
import logging
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from typing import Protocol
from urllib.parse import quote

import numpy as np

from ..config import SpeakerConfig
from .audio import alaw_encode, resample, to_pcm16, ulaw_encode, wav_bytes

log = logging.getLogger(__name__)


class SpeakerError(Exception):
    """Понятная человеку причина, почему динамик не сработал."""


class _StaleNonce(Exception):
    def __init__(self, challenge: str) -> None:
        super().__init__(challenge)
        self.challenge = challenge


class Speaker(Protocol):
    name: str

    def play(self, samples: np.ndarray, rate: int) -> None:
        """Проигрывает звук (моно float32) до конца; при ошибке — SpeakerError."""
        ...


def build_speaker(config: SpeakerConfig) -> Speaker:
    return HikvisionSpeaker(config) if config.kind == "hikvision" else LocalSpeaker(config.name, config.device)


# Один динамик (камера или звуковая карта) — одна фраза за раз, даже если он прописан в нескольких
# пресетах: две сессии двустороннего звука камера не примет, а на звуковой карте фразы смешаются.
_LOCKS: dict[tuple, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _endpoint_lock(*key: object) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class LocalSpeaker:
    """Физический динамик, подключённый к компьютеру с сервером.

    На сервере без рабочего стола звук идёт через ALSA (aplay); device — устройство ALSA,
    например plughw:1,0 (список: aplay -l). Без device — paplay, aplay или ffplay по умолчанию.
    """

    _PLAYERS = (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"])

    def __init__(self, name: str = "Динамик сервера", device: str = "") -> None:
        self.name = name
        self._device = device.strip()
        self._lock = _endpoint_lock("local", self._device)

    def _command(self) -> list[str]:
        if self._device:
            if not shutil.which("aplay"):
                raise SpeakerError("для выбора звуковой карты нужен aplay (пакет alsa-utils)")
            return ["aplay", "-q", "-D", self._device]
        player = next((p for p in self._PLAYERS if shutil.which(p[0])), None)
        if player is None:
            raise SpeakerError("на сервере нет проигрывателя звука (aplay из alsa-utils, paplay или ffplay)")
        return player

    def probe(self) -> dict:
        return {"player": " ".join(self._command())}

    def play(self, samples: np.ndarray, rate: int) -> None:
        command = self._command()
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes(samples, rate))
            with self._lock:
                result = subprocess.run([*command, path], capture_output=True, timeout=samples.size / rate + 15)
            if result.returncode:
                raise SpeakerError(f"{command[0]}: {result.stderr.decode(errors='replace').strip() or 'ошибка'}")
        finally:
            os.unlink(path)


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        el.tag = el.tag.rsplit("}", 1)[-1]
    return root


class _DigestAuth:
    """HTTP Digest (RFC 2617, MD5, qop=auth) — так авторизуется ISAPI Hikvision; Basic — если камера просит его."""

    def __init__(self, username: str, password: str) -> None:
        self._username, self._password = username, password
        self._challenge: dict[str, str] | None = None
        self._basic = False
        self._nc = 0

    def set_challenge(self, header: str) -> None:
        if header.lower().startswith("basic"):
            self._basic = True
            return
        self._challenge = dict(re.findall(r'(\w+)="?([^",]*)"?', header.partition(" ")[2]))
        self._nc = 0

    @property
    def ready(self) -> bool:
        return self._basic or self._challenge is not None

    def header(self, method: str, uri: str) -> str:
        if self._basic:
            return "Basic " + base64.b64encode(f"{self._username}:{self._password}".encode()).decode()
        assert self._challenge is not None
        c = self._challenge
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
        ha1 = md5(f"{self._username}:{c.get('realm', '')}:{self._password}")
        ha2 = md5(f"{method}:{uri}")
        self._nc += 1
        nc, cnonce = f"{self._nc:08x}", os.urandom(8).hex()
        qop = "auth" if "auth" in c.get("qop", "").split(",") else None
        response = md5(f"{ha1}:{c['nonce']}:{nc}:{cnonce}:{qop}:{ha2}") if qop else md5(f"{ha1}:{c['nonce']}:{ha2}")
        parts = [f'username="{self._username}"', f'realm="{c.get("realm", "")}"', f'nonce="{c["nonce"]}"',
                 f'uri="{uri}"', f'response="{response}"', "algorithm=MD5"]
        if qop:
            parts += [f"qop={qop}", f"nc={nc}", f'cnonce="{cnonce}"']
        if "opaque" in c:
            parts.append(f'opaque="{c["opaque"]}"')
        return "Digest " + ", ".join(parts)


_CODECS = {"G.711ulaw": "ulaw", "G.711alaw": "alaw", "G.711U": "ulaw", "G.711A": "alaw", "PCM": "pcm"}


class HikvisionSpeaker:
    """Динамик, подключённый к IP-камере Hikvision (выход Audio Out или встроенный).

    Звук передаётся как «двусторонний звук» (ISAPI TwoWayAudio): open → audioData → close.
    Кодек берётся из настроек камеры; поддерживаются G.711 µ-law/A-law и PCM.
    Неверный пароль запоминается: повторные попытки привели бы к блокировке IP камерой
    (а значит, и видео с неё), поэтому до исправления настроек динамик не стучится.
    """

    _CHUNK_S = 0.02
    _LEAD_S = 0.25  # насколько опережать реальное время при отправке

    def __init__(self, config: SpeakerConfig) -> None:
        self.name = config.name
        self._config = config
        password = config.password.get_secret_value() if config.password else ""
        self._auth = _DigestAuth(config.username, password)
        self._lock = _endpoint_lock("hikvision", config.host, config.port, config.channel)
        self._auth_failed = False
        self._base = f"/ISAPI/System/TwoWayAudio/channels/{config.channel}"

    # --- HTTP ---

    def _connection(self) -> http.client.HTTPConnection:
        c = self._config
        if c.https:
            return http.client.HTTPSConnection(c.host, c.port, timeout=c.timeout_s,
                                               context=ssl._create_unverified_context())  # самоподписанный сертификат
        return http.client.HTTPConnection(c.host, c.port, timeout=c.timeout_s)

    def _request(self, method: str, path: str, body: bytes = b"") -> bytes:
        if not self._config.password:
            raise SpeakerError("не задан пароль динамика — камеру не трогаем, чтобы она не заблокировала IP")
        if self._auth_failed:
            raise SpeakerError("неверный логин или пароль динамика — исправьте в настройках (повторных попыток "
                               "не будет, чтобы камера не заблокировала IP)")
        for attempt in range(2):
            conn = self._connection()
            try:
                headers = {"Content-Type": "application/xml"} if body else {}
                if self._auth.ready:
                    headers["Authorization"] = self._auth.header(method, path)
                conn.request(method, path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except (OSError, http.client.HTTPException) as e:
                raise SpeakerError(f"нет связи с {self._config.host}:{self._config.port} ({e})") from e
            finally:
                conn.close()
            if resp.status == 401 and attempt == 0:
                self._auth.set_challenge(resp.headers.get("WWW-Authenticate", ""))
                continue
            self._check(resp.status, data)
            return data
        raise AssertionError

    def _check(self, status: int, data: bytes) -> None:
        if status < 300:
            return
        if status == 401:
            self._auth_failed = True
            raise SpeakerError("неверный логин или пароль (Hikvision блокирует IP после нескольких ошибок)")
        if status == 403:
            raise SpeakerError("нет прав: нужен пользователь с правом «Двусторонний звук» (например, администратор)")
        if status == 404:
            raise SpeakerError("камера не поддерживает двусторонний звук (нет выхода на динамик?)")
        detail = ""
        try:
            root = _strip_ns(ET.fromstring(data))
            detail = " ".join(filter(None, (root.findtext("statusString"), root.findtext("subStatusCode"))))
        except ET.ParseError:
            pass
        raise SpeakerError(f"камера ответила {status} {detail}".strip())

    # --- двусторонний звук ---

    def probe(self) -> dict:
        """Настройки канала двустороннего звука — без воспроизведения."""
        root = _strip_ns(ET.fromstring(self._request("GET", self._base)))
        codec = root.findtext("audioCompressionType") or "?"
        rate_khz = root.findtext("audioSamplingRate")
        return {"codec": codec, "supported": codec in _CODECS, "enabled": root.findtext("enabled"),
                "sample_rate": int(float(rate_khz) * 1000) if rate_khz else 8000}

    def play(self, samples: np.ndarray, rate: int) -> None:
        with self._lock:
            info = self.probe()
            if not info["supported"]:
                raise SpeakerError(f"в камере выбран аудиокодек {info['codec']}; поставьте G.711ulaw "
                                   "(Конфигурация → Видео/Аудио → Аудио → Кодирование аудио)")
            kind = _CODECS[info["codec"]]
            out_rate = info["sample_rate"] if kind == "pcm" else 8000
            audio = resample(samples, rate, out_rate)
            payload, bytes_per_s = {
                "ulaw": lambda: (ulaw_encode(audio), out_rate),
                "alaw": lambda: (alaw_encode(audio), out_rate),
                "pcm": lambda: (to_pcm16(audio).tobytes(), out_rate * 2),
            }[kind]()

            session = self._open()
            query = f"?sessionId={quote(session)}" if session else ""
            try:
                try:
                    self._send_audio(f"{self._base}/audioData{query}", payload, bytes_per_s)
                except _StaleNonce as e:
                    # одноразовый nonce устарел между open и audioData — повторяем с новым
                    self._auth.set_challenge(e.challenge)
                    self._send_audio(f"{self._base}/audioData{query}", payload, bytes_per_s, stale_retry=True)
            finally:
                try:
                    self._request("PUT", f"{self._base}/close{query}")
                except SpeakerError as e:
                    log.warning("%s: не удалось закрыть сессию звука: %s", self.name, e)

    def _open(self) -> str | None:
        try:
            data = self._request("PUT", f"{self._base}/open")
        except SpeakerError:
            if self._auth_failed:
                raise
            # прошлая сессия могла остаться открытой (обрыв связи) — закрываем и пробуем снова
            self._request("PUT", f"{self._base}/close")
            data = self._request("PUT", f"{self._base}/open")
        try:
            return _strip_ns(ET.fromstring(data)).findtext("sessionId") if data.strip() else None
        except ET.ParseError:
            return None

    def _send_audio(self, path: str, payload: bytes, bytes_per_s: int, stale_retry: bool = False) -> None:
        """Отправляет звук в темпе воспроизведения: если отдать всё сразу, камера обрезает конец."""
        chunk = max(int(bytes_per_s * self._CHUNK_S), 1)
        conn = self._connection()
        try:
            conn.putrequest("PUT", path, skip_accept_encoding=True)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(len(payload)))
            if self._auth.ready:
                conn.putheader("Authorization", self._auth.header("PUT", path))
            conn.endheaders()
            started = time.monotonic()
            for offset in range(0, len(payload), chunk):
                due = started + offset / bytes_per_s - self._LEAD_S
                if (wait := due - time.monotonic()) > 0:
                    time.sleep(wait)
                conn.send(payload[offset:offset + chunk])
            resp = conn.getresponse()
            data = resp.read()
            # дождаться, пока камера доиграет буфер, иначе close обрежет конец фразы
            if (rest := started + len(payload) / bytes_per_s + 0.3 - time.monotonic()) > 0:
                time.sleep(rest)
        except (OSError, http.client.HTTPException) as e:
            raise SpeakerError(f"обрыв при передаче звука ({e})") from e
        finally:
            conn.close()
        if resp.status == 401 and not stale_retry:
            raise _StaleNonce(resp.headers.get("WWW-Authenticate", ""))
        self._check(resp.status, data)
