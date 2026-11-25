# SPDX-License-Identifier: GPL-3.0-or-later
import http.client
import io
import json
import socket
from pathlib import Path

import pytest
from PIL import Image

from rgbhub.core.interfaces import ManualRuntime
from rgbhub.core.json_store import JsonSettings
from rgbhub.core.web import MAX_PICTURE_BYTES, SettingsServer


@pytest.fixture
def web(tmp_path):
    page = tmp_path / "page"
    page.mkdir()
    (page / "index.html").write_text("<html>RGB Hub</html>")
    (page / "app.js").write_text("// app")
    (tmp_path / "secret.txt").write_text("not for the web")
    store = JsonSettings(tmp_path / "settings.json")
    calls = []
    server = SettingsServer(
        store,
        ManualRuntime(),
        page,
        tmp_path / "pictures",
        on_quit=lambda: calls.append("quit"),
        on_restart=lambda: calls.append("restart"),
    )
    url = server.start()
    yield server, store, url, calls, tmp_path
    server.stop()


def request(server, method, path, body=None, headers=None, token=True, host=None):
    """One request; returns (status, headers, body bytes)."""
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    all_headers = {"Host": host or f"127.0.0.1:{server.port}"}
    if token:
        all_headers["X-RGBHub-Token"] = server.token
    all_headers.update(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    connection.request(method, path, body=body, headers=all_headers)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, response.headers, data


def png_bytes():
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, "PNG")
    return out.getvalue()


def test_start_returns_the_local_url_with_the_token(web):
    server, _, url, _, _ = web
    assert url == f"http://127.0.0.1:{server.port}/?t={server.token}"
    assert server.port != 0 and len(server.token) >= 20


def test_state_needs_token(web):
    server = web[0]
    assert request(server, "GET", "/api/state", token=False)[0] == 403
    bad = {"X-RGBHub-Token": "wrong"}
    assert request(server, "GET", "/api/state", token=False, headers=bad)[0] == 403


def test_page_needs_token(web):
    server = web[0]
    assert request(server, "GET", "/", token=False)[0] == 403
    assert request(server, "GET", "/?t=wrong", token=False)[0] == 403
    status, headers, body = request(server, "GET", f"/?t={server.token}", token=False)
    assert status == 200 and b"RGB Hub" in body
    # The page's own files come with the cookie the page was served with.
    cookie = headers["Set-Cookie"].split(";")[0]
    assert request(server, "GET", "/app.js", token=False)[0] == 403
    assert request(server, "GET", "/app.js", token=False, headers={"Cookie": cookie})[0] == 200


def test_wrong_host_refused(web):
    server = web[0]
    for host in ("evil.example", f"localhost:{server.port}", "127.0.0.1:1"):
        assert request(server, "GET", "/api/state", host=host)[0] == 403
    assert request(server, "GET", f"/?t={server.token}", host="evil.example")[0] == 403


def test_state_has_settings_and_status(web):
    server, store, _, _, _ = web
    store.set("status", json.dumps({"devices": [{"id": "OMEN-PC"}]}))
    status, _, body = request(server, "GET", "/api/state")
    data = json.loads(body)
    assert status == 200
    assert data["status"] == {"devices": [{"id": "OMEN-PC"}]}
    assert data["settings"]["lcd-brightness"] == 80 and "status" not in data["settings"]


def test_set_valid_value_saved(web):
    server, store, _, _, tmp_path = web
    status, _, _ = request(server, "POST", "/api/set", {"key": "lcd-brightness", "value": 42})
    assert status == 204 and store.get("lcd-brightness") == 42
    assert json.loads((tmp_path / "settings.json").read_text()) == {"lcd-brightness": 42}
    colours = {"HP Omen 30L/Fan": "#FF0000"}
    request(server, "POST", "/api/set", {"key": "zone-colours", "value": colours})
    assert store.get("zone-colours") == colours


def test_set_unknown_key_400(web):
    server, store, _, _, tmp_path = web
    assert request(server, "POST", "/api/set", {"key": "nope", "value": 1})[0] == 400
    assert not (tmp_path / "settings.json").exists()


def test_set_wrong_type_400(web):
    server, store, _, _, tmp_path = web
    reply = request(server, "POST", "/api/set", {"key": "lcd-brightness", "value": "80"})
    assert reply[0] == 400
    bad_table = {"key": "device-speeds", "value": {"RAM": "fast"}}
    assert request(server, "POST", "/api/set", bad_table)[0] == 400
    assert request(server, "POST", "/api/set", {"key": "lcd-enabled", "value": 1})[0] == 400
    assert store.get("lcd-brightness") == 80 and store.get("device-speeds") == {}
    assert not (tmp_path / "settings.json").exists()


def test_set_status_400(web):
    server, store, _, _, _ = web
    for key in ("status", "focused-app"):
        assert request(server, "POST", "/api/set", {"key": key, "value": "{}x"})[0] == 400
    assert store.get("status") == "{}" and store.get("focused-app") == ""


def test_set_without_token_or_bad_body_writes_nothing(web):
    server, store, _, _, _ = web
    reply = request(server, "POST", "/api/set", {"key": "lcd-brightness", "value": 5}, token=False)
    assert reply[0] == 403 and store.get("lcd-brightness") == 80
    assert request(server, "POST", "/api/set", b"not json")[0] == 400
    assert request(server, "POST", "/api/set", b"[1, 2]")[0] == 400
    assert request(server, "POST", "/api/set", {"value": 5})[0] == 400


def test_set_goes_through_the_runtime(tmp_path):
    queued = []

    class Recorder(ManualRuntime):
        def call_soon(self, fn):
            queued.append(fn)

    store = JsonSettings(tmp_path / "settings.json")
    server = SettingsServer(store, Recorder(), tmp_path, tmp_path / "pictures")
    server.start()
    try:
        assert request(server, "POST", "/api/set", {"key": "lcd-fit", "value": "fit"})[0] == 204
        assert store.get("lcd-fit") == "fill"  # not yet: only on the loop
        queued.pop()()
        assert store.get("lcd-fit") == "fit"
    finally:
        server.stop()


def test_set_entries_merges_on_the_loop(web):
    server, store, _, _, _ = web
    url = "/api/set-entries"
    red = {"key": "zone-colours", "entries": {"A/1": "#FF0000"}}
    assert request(server, "POST", url, red)[0] == 204
    request(server, "POST", url, {"key": "zone-colours", "entries": {"B/1": "#00FF00"}})
    assert store.get("zone-colours") == {"A/1": "#FF0000", "B/1": "#00FF00"}
    store.set("zone-colours", {**store.get("zone-colours"), "C/1": "#0000FF"})  # Load Default
    request(server, "POST", url, {"key": "zone-colours", "entries": {"A/1": "#FFFFFF"}})
    assert store.get("zone-colours") == {"A/1": "#FFFFFF", "B/1": "#00FF00", "C/1": "#0000FF"}


def test_set_entries_merges_into_the_table_as_it_is_when_applied(tmp_path):
    queued = []

    class Recorder(ManualRuntime):
        def call_soon(self, fn):
            queued.append(fn)

    store = JsonSettings(tmp_path / "settings.json")
    server = SettingsServer(store, Recorder(), tmp_path, tmp_path / "pictures")
    server.start()
    try:
        body = {"key": "device-speeds", "entries": {"RAM": 10}}
        assert request(server, "POST", "/api/set-entries", body)[0] == 204
        store.set("device-speeds", {"IQUNIX MG65": 70})  # changed before the loop gets to it
        queued.pop()()
        assert store.get("device-speeds") == {"IQUNIX MG65": 70, "RAM": 10}
    finally:
        server.stop()


def test_set_entries_refusals_400(web):
    server, store, _, _, tmp_path = web
    url = "/api/set-entries"
    for body in (
        {"key": "nope", "entries": {}},
        {"key": "status", "entries": {}},
        {"key": "lcd-brightness", "entries": {"x": 1}},  # not a table
        {"key": "device-speeds", "entries": {"RAM": "fast"}},
        {"key": "device-speeds", "entries": [1]},
        {"key": "device-speeds", "value": {"RAM": 1}},
    ):
        assert request(server, "POST", url, body)[0] == 400, body
    assert (
        request(server, "POST", url, {"key": "device-speeds", "entries": {}}, token=False)[0] == 403
    )
    assert not (tmp_path / "settings.json").exists()


def test_picture_upload_saved_and_path_returned(web):
    server, _, _, _, tmp_path = web
    data = png_bytes()
    status, _, body = request(
        server, "POST", "/api/picture", data, headers={"X-Filename": "my%20cat.PNG"}
    )
    assert status == 200
    path = Path(json.loads(body)["path"])
    assert path.parent == tmp_path / "pictures"
    assert path.suffix == ".png" and len(path.stem) == 12
    assert path.read_bytes() == data


def test_picture_extension_comes_from_its_contents(web):
    server, _, _, _, tmp_path = web
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(out, "JPEG")
    status, _, body = request(
        server, "POST", "/api/picture", out.getvalue(), headers={"X-Filename": "x.exe"}
    )
    assert status == 200 and json.loads(body)["path"].endswith(".jpg")
    assert Path(json.loads(body)["path"]).is_absolute()
    tiff = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(tiff, "TIFF")
    assert request(server, "POST", "/api/picture", tiff.getvalue())[0] == 400


def test_picture_not_an_image_400(web):
    server, _, _, _, tmp_path = web
    reply = request(server, "POST", "/api/picture", b"hello", headers={"X-Filename": "x.png"})
    assert reply[0] == 400
    assert list((tmp_path / "pictures").glob("*")) == []  # nothing kept (the folder may not exist)


def test_picture_too_big_refused(web):
    server = web[0]
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.putrequest("POST", "/api/picture", skip_host=True)
    connection.putheader("Host", f"127.0.0.1:{server.port}")
    connection.putheader("X-RGBHub-Token", server.token)
    connection.putheader("Content-Length", str(MAX_PICTURE_BYTES + 1))
    connection.endheaders()  # the body is never sent: the size alone is refused
    assert connection.getresponse().status == 413
    connection.close()


def test_stranger_refused_before_the_body_is_read(web):
    server = web[0]
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.putrequest("POST", "/api/picture", skip_host=True)
    connection.putheader("Host", f"127.0.0.1:{server.port}")
    connection.putheader("Content-Length", str(MAX_PICTURE_BYTES))
    connection.endheaders()  # no token, no body: refused at once, not waiting for 20 MB
    assert connection.getresponse().status == 403
    connection.close()


def test_port_is_not_shared(web):
    server = web[0]
    assert server._server.allow_reuse_address is False
    other = socket.socket()
    with pytest.raises(OSError):
        other.bind(("127.0.0.1", server.port))
    other.close()


def test_picture_shown_only_if_it_is_a_chosen_one(web):
    server, store, _, _, tmp_path = web
    picture = tmp_path / "chosen.png"
    picture.write_bytes(png_bytes())
    query = f"/api/image?path={picture}"
    assert request(server, "GET", query)[0] == 404
    store.set("lcd-recent-images", [str(picture)])
    status, headers, body = request(server, "GET", query)
    assert status == 200 and body == picture.read_bytes()
    assert headers["Content-Type"] == "image/png"
    assert request(server, "GET", f"/api/image?path={tmp_path / 'secret.txt'}")[0] == 404


def page_cookie(server):
    """The cookie the page is served with, as a request header."""
    headers = request(server, "GET", f"/?t={server.token}", token=False)[1]
    return {"Cookie": headers["Set-Cookie"].split(";")[0]}


def test_cookie_opens_the_page_files_only(web):
    server = web[0]
    cookie = page_cookie(server)
    assert server.token not in cookie["Cookie"]  # not the key to the interface
    assert request(server, "GET", "/api/state", token=False, headers=cookie)[0] == 403
    token_cookie = {"Cookie": f"rgbhub={server.token}"}
    assert request(server, "GET", "/app.js", token=False, headers=token_cookie)[0] == 403


def test_static_path_traversal_404(web):
    server = web[0]
    cookie = page_cookie(server)
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/nope.js"):
        assert request(server, "GET", path, headers=cookie)[0] == 404, path


def test_quit_and_restart_call_back(web):
    server, _, _, calls, _ = web
    assert request(server, "POST", "/api/quit")[0] == 204
    assert request(server, "POST", "/api/restart")[0] == 204
    assert calls == ["quit", "restart"]
    assert request(server, "POST", "/api/quit", token=False)[0] == 403
    assert calls == ["quit", "restart"]


def test_unknown_api_404(web):
    server = web[0]
    assert request(server, "GET", "/api/nothing")[0] == 404
    assert request(server, "GET", "/api/set")[0] == 405
