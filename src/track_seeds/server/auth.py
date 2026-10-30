"""Аккаунты веб-интерфейса: разработчик (admin) и клиенты (client, только просмотр и отчёты).

- access — подписанный JWT (HS256) на 15 минут, refresh — случайная строка, в базе только её хеш.
  Refresh меняется при каждом обновлении; старый, предъявленный ещё раз, — признак кражи: сессия
  закрывается (кроме минуты после смены — две вкладки обновляют токен одновременно).
- Пароли — scrypt. Неверный вход — пауза, с одного адреса не больше 10 ошибок за 15 минут.
- Клиент регистрируется по одноразовой ссылке-приглашению с ограниченным сроком.
- Второй фактор (TOTP: Google Authenticator, Яндекс Ключ и т.п.) — по желанию.
- Ключи виджета (Scriptable на iPhone) — только состояние дробилок, отзываются по одному.

Всё в DATA_DIR/accounts.db (права 600), не в config/: config/ копируют с ПК на сервер,
а клиенты регистрируются на сервере.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2

log = logging.getLogger(__name__)

ACCESS_TTL_S = 15 * 60
REFRESH_TTL_S = {"admin": 7 * 86400, "client": 90 * 86400}  # продлевается, пока сессией пользуются
REFRESH_GRACE_S = 60
INVITE_HOURS = (1, 24, 72, 168)
WIDGET_PREFIX = "tsw_"
ISSUER = "track-seeds"
_LOGIN = re.compile(r"^[\w.@-]{3,40}$")
_WEAK = {"12345678", "123456789", "1234567890", "password", "qwertyui", "qwerty123", "11111111", "00000000",
         "admin123", "password1", "йцукенгш"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT UNIQUE NOT NULL,
    name       TEXT NOT NULL,
    role       TEXT NOT NULL,
    password   TEXT NOT NULL,
    totp       TEXT,
    totp_new   TEXT,
    totp_step  INTEGER NOT NULL DEFAULT 0,
    disabled   INTEGER NOT NULL DEFAULT 0,
    created    REAL NOT NULL,
    last_seen  REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    id        TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    refresh   TEXT NOT NULL,
    previous  TEXT,
    rotated   REAL,
    created   REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires   REAL NOT NULL,
    ip        TEXT,
    agent     TEXT
);
CREATE TABLE IF NOT EXISTS invites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT UNIQUE NOT NULL,
    note       TEXT NOT NULL,
    user_id    INTEGER REFERENCES users (id) ON DELETE CASCADE,
    created    REAL NOT NULL,
    expires    REAL NOT NULL,
    used       REAL,
    used_by    INTEGER
);
CREATE TABLE IF NOT EXISTS widgets (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token     TEXT UNIQUE NOT NULL,
    name      TEXT NOT NULL,
    created   REAL NOT NULL,
    last_used REAL
);
CREATE TABLE IF NOT EXISTS auth_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    action   TEXT NOT NULL,
    ok       INTEGER NOT NULL,
    user_id  INTEGER,
    username TEXT,
    ip       TEXT,
    agent    TEXT,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS auth_log_ts ON auth_log (ts);
"""


class AuthError(Exception):
    """Отказ, который можно показать пользователю; code — для интерфейса (например, нужен код 2FA)."""

    def __init__(self, message: str, status: int = 401, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Principal:
    """Кто делает запрос. user_id None — вход без пароля с этого компьютера (разработчик ещё не заведён)."""

    role: str  # admin | client
    user_id: int | None = None
    session_id: str | None = None
    username: str = ""
    name: str = ""
    widget: bool = False  # ключ виджета: только состояние дробилок

    @property
    def admin(self) -> bool:
        return self.role == "admin"


# --- пароли, токены, коды ---

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${2 ** 14}$8$1${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = _unb64(digest)
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_password(password: str, username: str = "") -> None:
    if len(password) < 8:
        raise AuthError("Пароль — не короче 8 символов", 422)
    if len(password) > 200:
        raise AuthError("Пароль слишком длинный", 422)
    if password.casefold() == username.casefold() or password.casefold() in _WEAK or len(set(password)) < 3:
        raise AuthError("Слишком простой пароль", 422)


def normalize_login(username: str) -> str:
    login = username.strip().casefold()
    if not _LOGIN.match(login):
        raise AuthError("Логин — от 3 до 40 символов: буквы, цифры, точка, дефис, _ и @", 422)
    return login


def jwt_encode(payload: dict, key: bytes) -> str:
    head = _b64(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(key, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(signature)}"


def jwt_decode(token: str, key: bytes, now: float | None = None) -> dict | None:
    """Полезная нагрузка, если подпись верна и срок не истёк; иначе None."""
    try:
        head, body, signature = token.split(".")
        expected = hmac.new(key, f"{head}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        if json.loads(_unb64(head)).get("alg") != "HS256":
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(payload, dict) or float(payload.get("exp", 0)) <= (now or time.time()):
        return None
    return payload


def totp_code(secret: str, step: int, digits: int = 6) -> str:
    """RFC 6238: код для шага времени (30 с)."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return f"{value:0{digits}d}"


def totp_match(secret: str, code: str, after_step: int, now: float | None = None) -> int | None:
    """Шаг, которому соответствует код (±30 с на расхождение часов), если он ещё не использован."""
    code = re.sub(r"\s", "", code or "")
    if not code.isdigit() or len(code) != 6:
        return None
    current = int((now or time.time()) // 30)
    for step in (current - 1, current, current + 1):
        if step > after_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def totp_uri(secret: str, username: str) -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote(ISSUER)}:{quote(username)}?secret={secret}&issuer={quote(ISSUER)}"


def qr_svg(text: str) -> str:
    """QR-код в SVG (кодирует OpenCV — без лишних библиотек)."""
    matrix = cv2.QRCodeEncoder.create().encode(text)
    size = matrix.shape[0]
    cells = "".join(f"M{x},{y}h1v1h-1z" for y in range(size) for x in range(size) if matrix[y, x] < 128)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 {size + 4} {size + 4}" '
            f'shape-rendering="crispEdges"><rect x="-2" y="-2" width="{size + 4}" height="{size + 4}" fill="#fff"/>'
            f'<path d="{cells}" fill="#000"/></svg>')


_THROTTLE_LIMITS = {"ip": 10, "user": 30}


class Throttle:
    """Неудачные попытки входа: с одного адреса — не больше 10 за 15 минут, на один логин — не больше 30."""

    WINDOW_S = 15 * 60

    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        fails = self._fails.setdefault(key, deque())
        while fails and fails[0] < now - self.WINDOW_S:
            fails.popleft()
        return fails

    def retry_after(self, ip: str, username: str = "") -> float:
        """Сколько секунд ещё ждать; 0 — можно пробовать."""
        now = time.time()
        wait = 0.0
        with self._lock:
            for kind, key in (("ip", ip), ("user", username)):
                if not key:
                    continue
                fails = self._recent(f"{kind}:{key}", now)
                if len(fails) >= _THROTTLE_LIMITS[kind]:
                    wait = max(wait, fails[-_THROTTLE_LIMITS[kind]] + self.WINDOW_S - now)
        return wait

    def fail(self, ip: str, username: str = "") -> None:
        now = time.time()
        with self._lock:
            for kind, key in (("ip", ip), ("user", username)):
                if key:
                    self._recent(f"{kind}:{key}", now).append(now)
            if len(self._fails) > 10000:  # не копить память на переборе с тысяч адресов
                self._fails = {k: v for k, v in self._fails.items() if v and v[-1] > now - self.WINDOW_S}

    def forget(self, username: str) -> None:
        with self._lock:
            self._fails.pop(f"user:{username}", None)


def _private_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    else:
        os.chmod(path, 0o600)


class AccountStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "accounts.db"
        _private_file(self.path)
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self.throttle = Throttle()
        self._dummy: str | None = None
        self._pruned = 0.0
        key = self._meta("jwt_key")
        if key is None:
            key = secrets.token_hex(32)
            self._set_meta("jwt_key", key)
        self._key = bytes.fromhex(key)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- служебное ---

    def _meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self._db.commit()

    def _write(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._db.execute(sql, params)
            self._db.commit()
            return cursor

    def _row(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def record(self, action: str, ok: bool, user_id: int | None = None, username: str | None = None,
               ip: str | None = None, agent: str | None = None, detail: str | None = None) -> None:
        """Журнал входов и изменений доступа (виден разработчику)."""
        self._write("INSERT INTO auth_log (ts, action, ok, user_id, username, ip, agent, detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), action, ok, user_id, username, ip, (agent or "")[:300] or None, detail))
        if not ok:
            log.warning("Доступ: %s отклонён (%s, %s)%s", action, username or "—", ip or "—",
                        f": {detail}" if detail else "")

    def journal(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT * FROM auth_log ORDER BY id DESC LIMIT ?", (min(limit, 500),))]

    def prune(self) -> None:
        now = time.time()
        self._pruned = now
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            self._db.execute("DELETE FROM invites WHERE expires < ?", (now - 30 * 86400,))
            self._db.execute("DELETE FROM auth_log WHERE ts < ?", (now - 180 * 86400,))
            self._db.commit()

    # --- пользователи ---

    def has_admin(self) -> bool:
        return self._row("SELECT 1 FROM users WHERE role = 'admin' AND disabled = 0 LIMIT 1") is not None

    def user(self, user_id: int) -> dict | None:
        row = self._row("SELECT * FROM users WHERE id = ?", (user_id,))
        return _user(row) if row else None

    def by_login(self, username: str) -> dict | None:
        row = self._row("SELECT * FROM users WHERE username = ?", (username.strip().casefold(),))
        return _user(row) if row else None

    def users(self) -> list[dict]:
        rows = self._rows(
            "SELECT u.*, (SELECT COUNT(*) FROM sessions s WHERE s.user_id = u.id AND s.expires > ?) AS sessions, "
            "(SELECT COUNT(*) FROM widgets w WHERE w.user_id = u.id) AS widgets FROM users u "
            "ORDER BY u.role, u.created", (time.time(),))
        return [_user(r) | {"sessions": r["sessions"], "widgets": r["widgets"]} for r in rows]

    def create_user(self, username: str, name: str, password: str, role: str = "client") -> dict:
        login = normalize_login(username)
        check_password(password, login)
        name = name.strip()[:80] or login
        try:
            cursor = self._write("INSERT INTO users (username, name, role, password, created) VALUES (?, ?, ?, ?, ?)",
                                 (login, name, role, hash_password(password), time.time()))
        except sqlite3.IntegrityError:
            raise AuthError("Такой логин уже занят", 409) from None
        return self.user(cursor.lastrowid)  # type: ignore[arg-type,return-value]

    def set_password(self, user_id: int, password: str, keep_session: str | None = None) -> None:
        """Новый пароль; все сессии, кроме keep_session, закрываются."""
        user = self.user(user_id)
        if user is None:
            raise AuthError("Пользователь не найден", 404)
        check_password(password, user["username"])
        self._write("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user_id))
        self.revoke_sessions(user_id, keep=keep_session)

    def update_user(self, user_id: int, disabled: bool | None = None, name: str | None = None) -> dict:
        user = self.user(user_id)
        if user is None:
            raise AuthError("Пользователь не найден", 404)
        if disabled and user["role"] == "admin" and self._admins() <= 1:
            raise AuthError("Это единственный разработчик — его не отключить", 409)
        if name is not None:
            self._write("UPDATE users SET name = ? WHERE id = ?", (name.strip()[:80] or user["username"], user_id))
        if disabled is not None:
            self._write("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if disabled:
                self.revoke_sessions(user_id)
        return self.user(user_id)  # type: ignore[return-value]

    def delete_user(self, user_id: int) -> None:
        user = self.user(user_id)
        if user is None:
            raise AuthError("Пользователь не найден", 404)
        if user["role"] == "admin" and self._admins() <= 1:
            raise AuthError("Это единственный разработчик — его не удалить", 409)
        self._write("DELETE FROM users WHERE id = ?", (user_id,))

    def _admins(self) -> int:
        return self._row("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0")[0]  # type: ignore[index]

    def bootstrap_admin(self, password: str) -> str | None:
        """WEB_PASSWORD из .env: при первом запуске — разработчик admin; сменили пароль в .env —
        пароль admin сбрасывается на новый (так восстанавливают доступ). Возвращает, что сделано."""
        fingerprint = self._meta("bootstrap")
        if fingerprint and verify_password(password, fingerprint):
            return None
        user = self.by_login("admin")
        if user is None:
            if len(password) < 8:
                raise AuthError("WEB_PASSWORD — не короче 8 символов")
            cursor = self._write("INSERT INTO users (username, name, role, password, created) VALUES (?, ?, ?, ?, ?)",
                                 ("admin", "Разработчик", "admin", hash_password(password), time.time()))
            self.record("bootstrap", True, cursor.lastrowid, "admin", detail="создан из WEB_PASSWORD")
            done = "создан разработчик admin (пароль — WEB_PASSWORD)"
        else:
            self._write("UPDATE users SET password = ?, role = 'admin', disabled = 0 WHERE id = ?",
                        (hash_password(password), user["id"]))
            self.revoke_sessions(user["id"])
            self.record("bootstrap", True, user["id"], "admin", detail="пароль сброшен на WEB_PASSWORD")
            done = "пароль admin сброшен на WEB_PASSWORD из .env"
        self._set_meta("bootstrap", hash_password(password))
        return done

    # --- вход и сессии ---

    def login(self, username: str, password: str, code: str = "", ip: str = "", agent: str = "",
              admin_allowed: bool = True) -> tuple[dict, str, str]:
        """Проверить логин, пароль и код 2FA → (пользователь, id сессии, refresh-токен)."""
        login = username.strip().casefold()[:60]
        if wait := self.throttle.retry_after(ip, login):
            raise AuthError(f"Слишком много неудачных попыток — повторите через {max(1, round(wait / 60))} мин", 429)
        row = self._row("SELECT * FROM users WHERE username = ?", (login,))
        if row is not None and row["role"] == "admin" and not admin_allowed:
            # пароль разработчика снаружи даже не проверяется — подбирать его из интернета бесполезно
            self.throttle.fail(ip)
            self.record("login", False, row["id"], login, ip, agent, "разработчик не из локальной сети")
            raise AuthError("Неверный логин или пароль")
        if self._dummy is None:
            self._dummy = hash_password(secrets.token_hex(8))
        valid = verify_password(password, row["password"] if row else self._dummy)  # время то же, что с логином
        if row is None or not valid:
            self.throttle.fail(ip, login)
            self.record("login", False, row["id"] if row else None, login, ip, agent, "неверный пароль")
            raise AuthError("Неверный логин или пароль")
        if row["disabled"]:
            self.record("login", False, row["id"], login, ip, agent, "аккаунт отключён")
            raise AuthError("Аккаунт отключён — обратитесь к разработчику", 403)
        if row["totp"]:
            if not code:
                raise AuthError("Введите код из приложения-аутентификатора", 401, "totp")
            step = totp_match(row["totp"], code, row["totp_step"])
            if step is None:
                self.throttle.fail(ip, login)
                self.record("login", False, row["id"], login, ip, agent, "неверный код 2FA")
                raise AuthError("Неверный код — возьмите свежий из приложения", 401, "totp")
            self._write("UPDATE users SET totp_step = ? WHERE id = ?", (step, row["id"]))
        self.throttle.forget(login)
        session_id, token = self.new_session(row["id"], row["role"], ip, agent)
        self.record("login", True, row["id"], login, ip, agent)
        return _user(row), session_id, token

    def check_current(self, user_id: int, password: str, ip: str = "") -> None:
        """Текущий пароль перед его сменой или отключением 2FA (с теми же ограничениями на перебор)."""
        row = self._row("SELECT username, password FROM users WHERE id = ?", (user_id,))
        if row is None:
            raise AuthError("Пользователь не найден", 404)
        if wait := self.throttle.retry_after(ip, row["username"]):
            raise AuthError(f"Слишком много неудачных попыток — повторите через {max(1, round(wait / 60))} мин", 429)
        if not verify_password(password, row["password"]):
            self.throttle.fail(ip, row["username"])
            self.record("password", False, user_id, row["username"], ip, detail="неверный текущий пароль")
            raise AuthError("Текущий пароль неверный", 403)

    def new_session(self, user_id: int, role: str, ip: str = "", agent: str = "") -> tuple[str, str]:
        session_id = secrets.token_urlsafe(12)
        secret = secrets.token_urlsafe(32)
        now = time.time()
        self._write("INSERT INTO sessions (id, user_id, refresh, created, last_seen, expires, ip, agent) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, user_id, _sha(secret), now, now, now + REFRESH_TTL_S[role], ip, (agent or "")[:300]))
        self._write("UPDATE users SET last_seen = ? WHERE id = ?", (now, user_id))
        if now - self._pruned > 6 * 3600:
            self.prune()
        return session_id, f"{session_id}.{secret}"

    def refresh(self, token: str, ip: str = "", agent: str = "") -> tuple[dict, str, str | None]:
        """Обменять refresh-токен → (пользователь, id сессии, новый refresh или None — оставить прежний)."""
        session_id, _, secret = (token or "").partition(".")
        now = time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT s.*, u.role, u.disabled, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.id = ?", (session_id,)).fetchone()
            if row is None or not secret or row["expires"] < now or row["disabled"]:
                raise AuthError("Сессия закончилась — войдите снова")
            digest = _sha(secret)
            if hmac.compare_digest(digest, row["refresh"]):
                fresh = secrets.token_urlsafe(32)
                self._db.execute("UPDATE sessions SET refresh = ?, previous = ?, rotated = ?, last_seen = ?, "
                                 "expires = ?, ip = ?, agent = ? WHERE id = ?",
                                 (_sha(fresh), digest, now, now, now + REFRESH_TTL_S[row["role"]], ip,
                                  (agent or "")[:300], session_id))
                self._db.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, row["user_id"]))
                self._db.commit()
                new_token: str | None = f"{session_id}.{fresh}"
            elif row["previous"] and hmac.compare_digest(digest, row["previous"]):
                if now - (row["rotated"] or 0) > REFRESH_GRACE_S:
                    self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                    self._db.commit()
                    self.record("refresh", False, row["user_id"], row["username"], ip, agent,
                                "старый refresh-токен предъявлен повторно — сессия закрыта")
                    raise AuthError("Сессия закрыта из соображений безопасности — войдите снова")
                new_token = None  # соседняя вкладка уже обновила токен, браузер хранит новый
            else:
                raise AuthError("Сессия закончилась — войдите снова")
        return self.user(row["user_id"]), session_id, new_token  # type: ignore[return-value]

    def access_token(self, user: dict, session_id: str) -> str:
        now = int(time.time())
        return jwt_encode({"sub": user["id"], "sid": session_id, "role": user["role"], "typ": "access",
                           "iat": now, "exp": now + ACCESS_TTL_S}, self._key)

    def authenticate(self, token: str) -> Principal | None:
        """Access-токен → кто это. Роль и блокировка — из базы: отзыв сессии действует сразу."""
        payload = jwt_decode(token, self._key)
        if payload is None or payload.get("typ") != "access":
            return None
        row = self._row("SELECT u.id, u.username, u.name, u.role, u.disabled, s.expires FROM sessions s "
                        "JOIN users u ON u.id = s.user_id WHERE s.id = ?", (str(payload.get("sid")),))
        if row is None or row["disabled"] or row["expires"] < time.time() or row["id"] != payload.get("sub"):
            return None
        return Principal(role=row["role"], user_id=row["id"], session_id=str(payload["sid"]),
                         username=row["username"], name=row["name"])

    def session_alive(self, session_id: str) -> bool:
        row = self._row("SELECT u.disabled FROM sessions s JOIN users u ON u.id = s.user_id "
                        "WHERE s.id = ? AND s.expires > ?", (session_id, time.time()))
        return row is not None and not row["disabled"]

    def logout(self, token: str) -> None:
        session_id = (token or "").partition(".")[0]
        if session_id:
            self._write("DELETE FROM sessions WHERE id = ?", (session_id,))

    def sessions(self, user_id: int) -> list[dict]:
        rows = self._rows("SELECT id, created, last_seen, expires, ip, agent FROM sessions "
                          "WHERE user_id = ? AND expires > ? ORDER BY last_seen DESC", (user_id, time.time()))
        return [dict(r) for r in rows]

    def revoke_session(self, user_id: int, session_id: str) -> None:
        self._write("DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id))

    def revoke_sessions(self, user_id: int, keep: str | None = None) -> None:
        self._write("DELETE FROM sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep))

    # --- приглашения ---

    def create_invite(self, note: str = "", hours: float = 24, user_id: int | None = None) -> tuple[str, dict]:
        """Одноразовая ссылка: регистрация одного клиента (или новый пароль для user_id)."""
        if not 0 < hours <= max(INVITE_HOURS):
            raise AuthError("Срок ссылки — от часа до недели", 422)
        if user_id is not None and self.user(user_id) is None:
            raise AuthError("Пользователь не найден", 404)
        code = secrets.token_urlsafe(24)
        now = time.time()
        cursor = self._write("INSERT INTO invites (code, note, user_id, created, expires) VALUES (?, ?, ?, ?, ?)",
                             (_sha(code), note.strip()[:120], user_id, now, now + hours * 3600))
        return code, self._invite(cursor.lastrowid)  # type: ignore[arg-type]

    def _invite(self, invite_id: int) -> dict:
        row = self._row("SELECT i.id, i.note, i.user_id, i.created, i.expires, i.used, i.used_by, "
                        "u.username AS for_user, b.username AS used_login FROM invites i "
                        "LEFT JOIN users u ON u.id = i.user_id LEFT JOIN users b ON b.id = i.used_by "
                        "WHERE i.id = ?", (invite_id,))
        return dict(row) if row else {}

    def invites(self) -> list[dict]:
        ids = self._rows("SELECT id FROM invites WHERE expires > ? OR used > ? ORDER BY id DESC LIMIT 100",
                         (time.time(), time.time() - 7 * 86400))
        return [self._invite(r["id"]) for r in ids]

    def revoke_invite(self, invite_id: int) -> None:
        self._write("DELETE FROM invites WHERE id = ? AND used IS NULL", (invite_id,))

    def invite(self, code: str) -> dict | None:
        """Действующее приглашение по коду из ссылки."""
        row = self._row("SELECT id FROM invites WHERE code = ? AND used IS NULL AND expires > ?",
                        (_sha(code or ""), time.time()))
        return self._invite(row["id"]) if row else None

    def register(self, code: str, username: str, name: str, password: str, ip: str = "",
                 agent: str = "") -> dict:
        """Регистрация по приглашению (или новый пароль по ссылке сброса). Ссылка гаснет сразу."""
        if wait := self.throttle.retry_after(ip):
            raise AuthError(f"Слишком много попыток — повторите через {max(1, round(wait / 60))} мин", 429)
        invite = self.invite(code)
        if invite is None:
            self.throttle.fail(ip)
            self.record("register", False, ip=ip, agent=agent, detail="ссылка недействительна")
            raise AuthError("Ссылка недействительна: срок истёк или по ней уже зарегистрировались", 410)
        if invite["user_id"] is not None:
            user = self.user(invite["user_id"])
            check_password(password, user["username"])  # type: ignore[index]
        else:
            user = None
            check_password(password, normalize_login(username))
        with self._lock:
            # ссылка — одному человеку: гасим до создания аккаунта, гонка двух запросов не пройдёт
            cursor = self._db.execute("UPDATE invites SET used = ? WHERE id = ? AND used IS NULL",
                                      (time.time(), invite["id"]))
            self._db.commit()
            if cursor.rowcount != 1:
                raise AuthError("По этой ссылке уже зарегистрировались", 410)
            try:
                if user is not None:
                    self.set_password(user["id"], password)
                    action = "reset"
                else:
                    user = self.create_user(username, name, password, "client")
                    action = "register"
            except AuthError:
                self._db.execute("UPDATE invites SET used = NULL WHERE id = ?", (invite["id"],))
                self._db.commit()
                raise
            self._db.execute("UPDATE invites SET used_by = ? WHERE id = ?", (user["id"], invite["id"]))
            self._db.commit()
        self.record(action, True, user["id"], user["username"], ip, agent, invite["note"] or None)
        return user

    # --- второй фактор ---

    def totp_begin(self, user_id: int) -> tuple[str, str]:
        """Новый секрет (ещё не включён) → (секрет, otpauth-ссылка для приложения)."""
        user = self.user(user_id)
        if user is None:
            raise AuthError("Пользователь не найден", 404)
        secret = base64.b32encode(os.urandom(20)).decode()
        self._write("UPDATE users SET totp_new = ? WHERE id = ?", (secret, user_id))
        return secret, totp_uri(secret, user["username"])

    def totp_enable(self, user_id: int, code: str, keep_session: str | None = None) -> None:
        row = self._row("SELECT totp_new, username FROM users WHERE id = ?", (user_id,))
        if row is None or not row["totp_new"]:
            raise AuthError("Сначала получите новый ключ", 409)
        step = totp_match(row["totp_new"], code, 0)
        if step is None:
            raise AuthError("Код не подходит — проверьте время на телефоне и введите свежий", 422)
        self._write("UPDATE users SET totp = totp_new, totp_new = NULL, totp_step = ? WHERE id = ?", (step, user_id))
        self.revoke_sessions(user_id, keep=keep_session)  # остальные входили без кода
        self.record("totp_on", True, user_id, row["username"])

    def totp_disable(self, user_id: int, password: str, ip: str = "") -> None:
        self.check_current(user_id, password, ip)
        row = self._row("SELECT username FROM users WHERE id = ?", (user_id,))
        self._write("UPDATE users SET totp = NULL, totp_new = NULL WHERE id = ?", (user_id,))
        self.record("totp_off", True, user_id, row["username"], ip)

    def totp_reset(self, user_id: int) -> None:
        """Из консоли: телефон с приложением потерян."""
        self._write("UPDATE users SET totp = NULL, totp_new = NULL WHERE id = ?", (user_id,))

    # --- ключи виджета ---

    def create_widget(self, user_id: int, name: str) -> tuple[str, dict]:
        token = WIDGET_PREFIX + secrets.token_urlsafe(32)
        cursor = self._write("INSERT INTO widgets (user_id, token, name, created) VALUES (?, ?, ?, ?)",
                             (user_id, _sha(token), name.strip()[:60] or "iPhone", time.time()))
        return token, self._widget(cursor.lastrowid)  # type: ignore[arg-type]

    def _widget(self, widget_id: int) -> dict:
        row = self._row("SELECT id, name, created, last_used FROM widgets WHERE id = ?", (widget_id,))
        return dict(row) if row else {}

    def widgets(self, user_id: int) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT id, name, created, last_used FROM widgets WHERE user_id = ? "
                                            "ORDER BY id DESC", (user_id,))]

    def revoke_widget(self, user_id: int, widget_id: int) -> None:
        self._write("DELETE FROM widgets WHERE id = ? AND user_id = ?", (widget_id, user_id))

    def widget_principal(self, token: str) -> Principal | None:
        row = self._row("SELECT w.id, w.last_used, u.id AS user_id, u.username, u.name, u.role, u.disabled "
                        "FROM widgets w JOIN users u ON u.id = w.user_id WHERE w.token = ?", (_sha(token),))
        if row is None or row["disabled"]:
            return None
        if time.time() - (row["last_used"] or 0) > 60:
            self._write("UPDATE widgets SET last_used = ? WHERE id = ?", (time.time(), row["id"]))
        return Principal(role=row["role"], user_id=row["user_id"], username=row["username"], name=row["name"],
                         widget=True)


def _user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "username": row["username"], "name": row["name"], "role": row["role"],
            "disabled": bool(row["disabled"]), "totp": bool(row["totp"]), "created": row["created"],
            "last_seen": row["last_seen"]}
