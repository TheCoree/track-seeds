"""MJPEG-превью с разметкой детекций в браузере — для отладки на сервере без GUI."""

import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

from .config import PreviewConfig

log = logging.getLogger(__name__)

_PAGE = b"""<!doctype html><meta charset="utf-8"><title>track-seeds</title>
<style>body{margin:0;background:#111;display:grid;place-items:center;height:100vh}
img{max-width:100%;max-height:100vh}</style><img src="/stream">"""


class PreviewServer:
    def __init__(self, settings: PreviewConfig) -> None:
        self._settings = settings
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0
        self._clients = 0
        self._server: ThreadingHTTPServer | None = None

    @property
    def has_clients(self) -> bool:
        return self._clients > 0

    def update(self, image: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self._settings.jpeg_quality])
        if not ok:
            return
        with self._cond:
            self._jpeg = buf.tobytes()
            self._seq += 1
            self._cond.notify_all()

    def start(self) -> None:
        self._server = ThreadingHTTPServer((self._settings.host, self._settings.port), self._handler())
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="preview", daemon=True).start()
        log.info("Превью: http://%s:%d/", self._settings.host, self._settings.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _next_frame(self, after_seq: int) -> tuple[int, bytes | None]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after_seq, timeout=5)
            return self._seq, self._jpeg

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                log.debug(format, *args)

            def do_GET(self) -> None:
                match self.path:
                    case "/":
                        self._reply(_PAGE, "text/html; charset=utf-8")
                    case "/snapshot.jpg":
                        _, jpeg = preview._next_frame(-1)
                        if jpeg is None:
                            self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Нет кадров")
                        else:
                            self._reply(jpeg, "image/jpeg")
                    case "/stream":
                        self._stream()
                    case _:
                        self.send_error(HTTPStatus.NOT_FOUND)

            def _reply(self, body: bytes, content_type: str) -> None:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _stream(self) -> None:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                preview._clients += 1
                seq = -1
                try:
                    while True:
                        seq, jpeg = preview._next_frame(seq)
                        if jpeg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: %d\r\n\r\n%s\r\n" % (len(jpeg), jpeg))
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    preview._clients -= 1

        return Handler
