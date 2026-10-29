"""Отправка в ntfy (https://docs.ntfy.sh/publish/)."""

import base64
import logging
import urllib.error
import urllib.request
from urllib.parse import urlencode

from ..config import NtfyConfig
from .base import Notification

log = logging.getLogger(__name__)


class NtfyNotifier:
    name = "ntfy"

    def __init__(self, settings: NtfyConfig) -> None:
        if not settings.topic:
            raise ValueError("Канал ntfy включён, но NTFY_TOPIC не задан")
        self._settings = settings
        self._endpoint = f"{settings.url.rstrip('/')}/{settings.topic}"
        self._headers: dict[str, str] = {}
        if settings.token:
            self._headers["Authorization"] = f"Bearer {settings.token.get_secret_value()}"
        elif settings.username and settings.password:
            creds = f"{settings.username}:{settings.password.get_secret_value()}".encode()
            self._headers["Authorization"] = f"Basic {base64.b64encode(creds).decode()}"

    def send(self, notification: Notification) -> None:
        if notification.image:
            try:
                self._send_with_image(notification)
                return
            except urllib.error.HTTPError as e:
                # Например, на self-hosted сервере не настроен attachment-cache-dir
                if e.code not in (400, 413):
                    raise
                log.warning("ntfy отклонил вложение (%s), отправляю без снимка", e.code)
        self._send_text(notification)

    def _params(self, n: Notification) -> dict[str, str]:
        # Параметры передаём в query string: заголовки HTTP не поддерживают кириллицу
        params = {"title": n.title, "priority": n.priority}
        if n.tags:
            params["tags"] = ",".join(n.tags)
        return params

    def _send_text(self, n: Notification) -> None:
        self._post(self._params(n), n.message.encode(), {"Content-Type": "text/plain; charset=utf-8"})

    def _send_with_image(self, n: Notification) -> None:
        params = self._params(n) | {"message": n.message, "filename": "snapshot.jpg"}
        self._post(params, n.image or b"", {"Content-Type": "image/jpeg"}, method="PUT")

    def _post(self, params: dict[str, str], body: bytes, headers: dict[str, str], method: str = "POST") -> None:
        req = urllib.request.Request(
            f"{self._endpoint}?{urlencode(params)}",
            data=body,
            headers=self._headers | headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=self._settings.timeout_s) as resp:
            resp.read()
