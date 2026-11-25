# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings page, served on this computer only.

Where there is no settings window of the desktop's own (Windows), the
settings are a web page: frontends/web, served from here, talking to the
small JSON interface below. Any platform may use it; it needs only the
standard library and Pillow.

Only this computer can reach it, and only the page that was opened with
the secret token (it is in the address start() returns):
- the server listens on 127.0.0.1 only;
- a request naming another host is refused (a web site cannot point a name
  of its own at 127.0.0.1 and read the page);
- GET / needs ?t=<token>; the page's own files need the cookie it sets
  (a separate secret that opens nothing else); /api/* needs the token in
  the X-RGBHub-Token header, which other web sites cannot send;
- these checks come before a request's body is read;
- no other program may listen on the same port (no address reuse; on
  Windows, exclusive use).

The interface:
    GET  /api/state      {"settings": {...}, "status": {...}}
    POST /api/set        {"key": ..., "value": ...}: one setting, checked first
    POST /api/set-entries {"key": ..., "entries": {...}}: some entries of a table
                         setting, merged into it when the change is made (so a
                         page that is a moment behind does not undo other changes)
    POST /api/picture    the bytes of a PNG, JPEG, GIF, BMP or WebP picture:
                         kept in the pictures folder; replies {"path": ...}
    GET  /api/image      ?path=...: one of the chosen pictures, for the page to show
    POST /api/quit, /api/restart

To add an endpoint, write one method marked @_route("METHOD", "/api/...").
"""

import argparse
import hashlib
import hmac
import http.cookies
import io
import json
import logging
import mimetypes
import os
import secrets
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from PIL import Image

from .settings_schema import DEFAULTS, valid

log = logging.getLogger(__name__)

MAX_PICTURE_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = 256 * 1024  # anything but a picture
TOKEN_HEADER = "X-RGBHub-Token"
COOKIE = "rgbhub"
NOT_WRITABLE = ("status", "focused-app")  # written by the hub and the platform only
# Pillow's name for a kind of picture -> the extension it is saved with
PICTURE_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "BMP": ".bmp", "WEBP": ".webp"}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any  # email.message.Message: case-insensitive get()
    body: bytes = b""


@dataclass
class Reply:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)


def _json(data: Any, status: int = 200) -> Reply:
    return Reply(status, json.dumps(data).encode())


def _error(status: int, message: str) -> Reply:
    return _json({"error": message}, status)


# (method, path) -> the SettingsServer method that answers it
_ROUTES: dict[tuple[str, str], Callable[["SettingsServer", Request], Reply]] = {}


def _route(method: str, path: str):
    def mark(fn):
        _ROUTES[(method, path)] = fn
        return fn

    return mark


class _LocalServer(ThreadingHTTPServer):
    """Our port is ours alone: no other program may listen on it as well."""

    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)  # Windows only
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class SettingsServer:
    def __init__(
        self,
        settings,
        runtime,
        page_dir: Path,
        pictures_dir: Path,
        on_quit: Callable[[], None] | None = None,
        on_restart: Callable[[], None] | None = None,
    ):
        """`settings` fits core.interfaces.Settings, `runtime` core.interfaces.Runtime:
        every change is made on its loop."""
        self._settings = settings
        self._runtime = runtime
        self._page_dir = Path(page_dir).resolve()
        self._pictures_dir = Path(pictures_dir).resolve()
        self._on_quit = on_quit
        self._on_restart = on_restart
        self.token = secrets.token_urlsafe(24)
        self._cookie_secret = secrets.token_urlsafe(24)  # opens the page's files only
        self.port = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # --- running -------------------------------------------------------------

    def start(self, port: int = 0) -> str:
        """Start serving on a daemon thread (port 0: any free port). Returns the
        page's address, token included."""
        owner = self

        class Handler(_Handler):
            server_owner = owner

        self._server = _LocalServer(("127.0.0.1", port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},  # how quickly stop() is noticed
            name="rgbhub-web",
            daemon=True,
        )
        self._thread.start()
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = self._thread = None

    # --- checks ----------------------------------------------------------------

    @staticmethod
    def _matches(given: str | None, secret: str) -> bool:
        return bool(given) and hmac.compare_digest(given.encode(), secret.encode())

    def _same(self, given: str | None) -> bool:
        return self._matches(given, self.token)

    def _host_ok(self, request: Request) -> bool:
        return request.headers.get("Host") == f"127.0.0.1:{self.port}"

    def _cookie_ok(self, request: Request) -> bool:
        cookies = http.cookies.SimpleCookie()
        try:
            cookies.load(request.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return False
        return COOKIE in cookies and self._matches(cookies[COOKIE].value, self._cookie_secret)

    def refuse(self, request: Request) -> Reply | None:
        """The refusal for a request from a stranger, or None. Needs only the
        request line and headers, so it is asked before the body is read."""
        if not self._host_ok(request):
            return _error(403, "wrong host")
        if request.path.startswith("/api/"):
            allowed = self._same(request.headers.get(TOKEN_HEADER))
        elif request.path == "/":
            allowed = self._same((request.query.get("t") or [""])[0])
        else:
            allowed = self._cookie_ok(request)
        return None if allowed else _error(403, "missing or wrong token")

    def answer(self, request: Request) -> Reply:
        """The reply to one request (called on the server's threads)."""
        refusal = self.refuse(request)
        if refusal is not None:
            return refusal
        if request.path.startswith("/api/"):
            handler = _ROUTES.get((request.method, request.path))
            if handler is None:
                known = any(path == request.path for _, path in _ROUTES)
                return _error(405 if known else 404, "no such request")
            return handler(self, request)
        if request.method != "GET":
            return _error(405, "the page only reads")
        if request.path == "/":
            reply = self._static("index.html")
            reply.headers["Set-Cookie"] = (
                f"{COOKIE}={self._cookie_secret}; Path=/; HttpOnly; SameSite=Strict"
            )
            return reply
        return self._static(request.path)

    def _static(self, path: str) -> Reply:
        """A file of the page, never from outside its folder."""
        parts = unquote(path).replace("\\", "/").split("/")
        if any(part in ("..", ".") or ":" in part for part in parts):
            return _error(404, "not found")
        target = (self._page_dir / "/".join(p for p in parts if p)).resolve()
        if not target.is_relative_to(self._page_dir) or not target.is_file():
            return _error(404, "not found")
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            kind = "text/javascript"
        if kind.startswith("text/"):
            kind += "; charset=utf-8"
        return Reply(200, target.read_bytes(), kind)

    # --- the interface -----------------------------------------------------------

    @_route("GET", "/api/state")
    def _state(self, request: Request) -> Reply:
        settings = {key: self._settings.get(key) for key in DEFAULTS if key != "status"}
        try:
            status = json.loads(self._settings.get("status") or "{}")
        except ValueError:
            status = {}
        return _json({"settings": settings, "status": status})

    @staticmethod
    def _change(request: Request, field: str) -> tuple[str, Any] | Reply:
        """The setting and value a change names, checked; or the refusal."""
        try:
            data = json.loads(request.body)
        except ValueError:
            return _error(400, "not JSON")
        if not isinstance(data, dict) or "key" not in data or field not in data:
            return _error(400, f'expected {{"key": ..., "{field}": ...}}')
        key, value = data["key"], data[field]
        if not isinstance(key, str) or key not in DEFAULTS or key in NOT_WRITABLE:
            return _error(400, f"cannot set {key!r}")
        if not valid(key, value):
            return _error(400, f"wrong kind of value for {key}")
        return key, value

    @_route("POST", "/api/set")
    def _set(self, request: Request) -> Reply:
        change = self._change(request, "value")
        if isinstance(change, Reply):
            return change
        key, value = change
        self._runtime.call_soon(lambda: self._settings.set(key, value))
        return Reply(204)

    @_route("POST", "/api/set-entries")
    def _set_entries(self, request: Request) -> Reply:
        change = self._change(request, "entries")
        if isinstance(change, Reply):
            return change
        key, entries = change
        if not isinstance(entries, dict):
            return _error(400, f"{key} is not a table")

        def merge() -> None:  # on the loop, into the table as it is then
            self._settings.set(key, {**self._settings.get(key), **entries})

        self._runtime.call_soon(merge)
        return Reply(204)

    @_route("POST", "/api/picture")
    def _picture(self, request: Request) -> Reply:
        data = request.body
        try:
            with Image.open(io.BytesIO(data)) as image:
                kind = image.format or ""
                image.verify()
        except Exception:
            return _error(400, "this file is not a picture")
        extension = PICTURE_EXTENSIONS.get(kind)
        if extension is None:
            return _error(400, "use a PNG, JPEG, GIF, BMP or WebP picture")
        target = self._pictures_dir / (hashlib.sha1(data).hexdigest()[:12] + extension)
        try:
            self._pictures_dir.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        except OSError as error:
            log.warning("Could not keep the picture %s: %s", target, error)
            return _error(500, "the picture could not be saved")
        return _json({"path": str(target)})

    @_route("GET", "/api/image")
    def _image(self, request: Request) -> Reply:
        """A picture to show on the page: only the ones the settings name."""
        path = (request.query.get("path") or [""])[0]
        chosen = {
            self._settings.get("lcd-image"),
            self._settings.get("auto-idle-image"),
            *self._settings.get("lcd-recent-images"),
        } - {""}
        kind = mimetypes.guess_type(path)[0] or ""
        if path not in chosen or not kind.startswith("image/"):
            return _error(404, "not found")
        try:
            return Reply(200, Path(path).read_bytes(), kind)
        except OSError:
            return _error(404, "not found")

    @_route("POST", "/api/quit")
    def _quit(self, request: Request) -> Reply:
        return self._call(self._on_quit)

    @_route("POST", "/api/restart")
    def _restart(self, request: Request) -> Reply:
        return self._call(self._on_restart)

    def _call(self, callback: Callable[[], None] | None) -> Reply:
        if callback is None:
            return _error(404, "not offered here")
        self._runtime.call_soon(callback)
        return Reply(204)


class _Handler(BaseHTTPRequestHandler):
    server_owner: SettingsServer
    server_version = "RGB-Hub"
    sys_version = ""

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def _serve(self) -> None:
        url = urlsplit(self.path)
        request = Request(self.command, url.path, parse_qs(url.query), self.headers)
        refusal = self.server_owner.refuse(request)
        if refusal is not None:
            self.close_connection = True  # the body is never read
            self._send(refusal)
            return
        limit = MAX_PICTURE_BYTES if url.path == "/api/picture" else MAX_BODY_BYTES
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            reply = _error(400, "bad Content-Length")
        elif length > limit:
            reply = _error(413, "too big")
            self.close_connection = True  # the body is not read
        else:
            request.body = self.rfile.read(length) if length else b""
            try:
                reply = self.server_owner.answer(request)
            except Exception:
                log.exception("The settings page's request %s %s failed", self.command, url.path)
                reply = _error(500, "something went wrong")
        self._send(reply)

    def _send(self, reply: Reply) -> None:
        self.send_response(reply.status)
        if reply.body or reply.status != 204:
            self.send_header("Content-Type", reply.content_type)
            self.send_header("Content-Length", str(len(reply.body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")  # the address holds the token
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'",
        )
        for name, value in reply.headers.items():
            self.send_header(name, value)
        self.end_headers()
        if reply.body:
            self.wfile.write(reply.body)

    def log_message(self, format: str, *args) -> None:
        log.debug("web: " + format, *args)


# --- for trying the page by hand -------------------------------------------------


def _page_dir() -> Path:
    """frontends/web in a checkout of the repository."""
    return Path(__file__).resolve().parents[3] / "frontends" / "web"


def main(argv: list[str] | None = None) -> None:
    from .json_store import JsonSettings
    from .thread_runtime import ThreadRuntime

    parser = argparse.ArgumentParser(
        prog="python -m rgbhub.core.web", description="Serve the settings page over a JSON file."
    )
    parser.add_argument("--settings", type=Path, required=True, help="the JSON settings file")
    parser.add_argument("--pictures", type=Path, help="where chosen pictures are kept")
    parser.add_argument("--page", type=Path, default=_page_dir(), help="the page's folder")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    runtime = ThreadRuntime()
    settings = JsonSettings(args.settings)
    runtime.watch_file(str(args.settings), settings.reload)
    pictures = args.pictures or args.settings.resolve().parent / "pictures"
    server = SettingsServer(settings, runtime, args.page, pictures, on_quit=runtime.stop)
    print(server.start(args.port), flush=True)
    try:
        runtime.run_until_stopped()
    finally:
        server.stop()


if __name__ == "__main__":
    main()
