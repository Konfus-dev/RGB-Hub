# SPDX-License-Identifier: GPL-3.0-or-later
"""The self-updater: follows `main`, never backwards, one folder per version,
rolls back when the new hub does not start. No real network calls here."""

import ast
import io
import json
import logging
import os
import stat
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

from rgbhub.updater import UpdateError
from rgbhub.updater import __main__ as updater_main
from rgbhub.updater.flow import update_once
from rgbhub.updater.github import GitHub
from rgbhub.updater.store import Store

OLD = "a" * 40
NEW = "b" * 40
NEWER = "c" * 40
UPDATER_DIR = Path(__file__).resolve().parents[1] / "rgbhub" / "updater"


def make_zip(files, top="RGB-Hub-xyz", extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/", "")
        for name, data in files.items():
            zf.writestr(f"{top}/{name}", data)
        for info, data in extra or []:
            zf.writestr(info, data)
    return buf.getvalue()


ZIP = make_zip({"service/rgbhub/__init__.py": "# hi\n", "README.md": "readme\n"})


class FakeGitHub:
    def __init__(self, latest=NEW, ahead=True, zip_bytes=ZIP):
        self.sha = latest
        self.ahead = ahead
        self.zip_bytes = zip_bytes
        self.downloads = []

    def latest(self, branch="main"):
        return self.sha

    def is_ahead(self, base, head):
        return self.ahead

    def download(self, sha, dest):
        self.downloads.append(sha)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.zip_bytes)


class FakeLayout:
    def __init__(self, current=OLD, check_ok=True, healthy=True, busy=False, changes=()):
        self.current = current
        self.check_ok = check_ok
        self.healthy = healthy
        self.is_busy = busy
        self.changes = list(changes)
        self.calls = []
        self.notes = []
        self.activated = []

    def prepare(self, version):
        self.calls.append(("prepare", version.name))

    def self_check(self, version):
        self.calls.append(("self_check", version.name))
        return self.check_ok

    def privileged_changes(self, old, new):
        return self.changes

    def request_privileged(self, new, changes):
        self.calls.append(("request_privileged", new.name, tuple(changes)))

    def activate(self, version):
        self.activated.append(version.name)
        self.current = version.name

    def restart_hub(self):
        self.calls.append(("restart",))

    def hub_healthy(self, wait_s):
        return self.healthy

    def busy(self):
        return self.is_busy

    def notify(self, text):
        self.notes.append(text)

    def current_commit(self):
        return self.current


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "root")
    s.version_dir(OLD).mkdir(parents=True)
    return s


# ---- flow ---------------------------------------------------------------


def test_up_to_date(store):
    gh, layout = FakeGitHub(latest=OLD), FakeLayout()
    assert update_once(gh, store, layout, health_wait_s=0) == "up-to-date"
    assert gh.downloads == [] and layout.activated == []


def test_never_goes_backwards(store):
    gh, layout = FakeGitHub(ahead=False), FakeLayout()
    assert update_once(gh, store, layout, health_wait_s=0) == "not-ahead"
    assert gh.downloads == [] and layout.activated == []
    assert not store.version_dir(NEW).exists()


def test_unknown_installed_commit_is_never_replaced(store):
    gh, layout = FakeGitHub(), FakeLayout(current=None)
    assert update_once(gh, store, layout, health_wait_s=0) == "failed"
    assert gh.downloads == [] and layout.activated == []


def test_updated(store):
    gh, layout = FakeGitHub(), FakeLayout()
    assert update_once(gh, store, layout, health_wait_s=0) == "updated"
    new = store.version_dir(NEW)
    assert (new / "service" / "rgbhub" / "__init__.py").read_text() == "# hi\n"
    assert (new / "README.md").exists()
    assert layout.activated == [NEW]
    assert ("restart",) in layout.calls
    state = store.load()
    assert state["current"] == NEW and state["previous"] == OLD and state["checked"]
    assert layout.notes == [f"RGB-Hub updated to {NEW[:7]}"]
    assert list(store.downloads.glob("*")) == []


def test_github_unreachable_keeps_current(store, caplog):
    def make_opener(error):
        def opener(req, timeout):
            raise error

        return opener

    errors = [
        urllib.error.URLError("no route"),
        urllib.error.HTTPError("u", 403, "rate limited", {}, None),
        urllib.error.HTTPError("u", 429, "too many", {}, None),
        urllib.error.HTTPError("u", 404, "not found", {}, None),
        TimeoutError("timed out"),
    ]
    for error in errors:
        layout = FakeLayout()
        store.save({**store.load(), "checked": "before"})
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="rgbhub.updater"):
            result = update_once(GitHub(opener=make_opener(error)), store, layout, 0)
        assert result == "failed"
        assert layout.activated == [] and layout.notes == []
        assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1
        assert store.load()["checked"] == "before"


def test_unknown_installed_commit_on_github_is_not_ahead(store):
    """compare/<installed>...<new> answering 404 means we cannot prove it is newer."""

    def opener(req, timeout):
        if "/compare/" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, None)
        return io.BytesIO(json.dumps({"sha": NEW}).encode())

    layout = FakeLayout()
    assert update_once(GitHub(opener=opener), store, layout, 0) == "not-ahead"
    assert layout.activated == []


def test_interrupted_unpack_never_activated(store):
    partial = store.versions / ".abc.partial"
    (partial / "service").mkdir(parents=True)
    store.downloads.mkdir(parents=True)
    (store.downloads / f"{NEW}.part").write_bytes(b"half")
    layout = FakeLayout()
    assert update_once(FakeGitHub(latest=OLD), store, layout, 0) == "up-to-date"
    assert not partial.exists()
    assert not (store.downloads / f"{NEW}.part").exists()
    assert layout.activated == []


def test_unpack_failure_leaves_no_version_folder(store):
    gh, layout = FakeGitHub(zip_bytes=b"not a zip"), FakeLayout()
    assert update_once(gh, store, layout, 0) == "failed"
    assert not store.version_dir(NEW).exists()
    assert [p.name for p in store.versions.iterdir()] == [OLD]
    assert layout.activated == []


def test_self_check_failure_marks_bad(store):
    layout = FakeLayout(check_ok=False)
    assert update_once(FakeGitHub(), store, layout, 0) == "failed"
    assert NEW in store.load()["bad"]
    assert layout.activated == []
    assert store.load()["current"] is None


def test_unhealthy_rolls_back(store):
    layout = FakeLayout(healthy=False)
    assert update_once(FakeGitHub(), store, layout, 0) == "rolled-back"
    assert layout.activated == [NEW, OLD]
    assert [c for c in layout.calls if c == ("restart",)] == [("restart",), ("restart",)]
    assert NEW in store.load()["bad"]
    assert layout.notes == [f"RGB-Hub update {NEW[:7]} didn't start; went back to {OLD[:7]}"]


def test_activate_error_rolls_back(store):
    class Broken(FakeLayout):
        def restart_hub(self):
            if self.current == NEW:
                raise OSError("restart failed")

    layout = Broken()
    assert update_once(FakeGitHub(), store, layout, 0) == "rolled-back"
    assert layout.activated == [NEW, OLD]


def test_bad_commit_skipped_until_main_moves(store):
    store.mark_bad(NEW)
    gh, layout = FakeGitHub(), FakeLayout()
    assert update_once(gh, store, layout, 0) == "bad-commit"
    assert gh.downloads == []
    gh.sha = NEWER
    assert update_once(gh, store, layout, 0) == "updated"
    assert layout.activated == [NEWER]


def test_busy_waits(store):
    gh, layout = FakeGitHub(), FakeLayout(busy=True)
    assert update_once(gh, store, layout, 0) == "busy"
    assert gh.downloads == [] and layout.activated == []
    layout.is_busy = False
    assert update_once(gh, store, layout, 0) == "updated"


def test_privileged_changes_requested_but_rest_applied(store):
    layout = FakeLayout(changes=["helper/rgbhub_ram_helper.py"])
    assert update_once(FakeGitHub(), store, layout, 0) == "updated"
    assert ("request_privileged", NEW, ("helper/rgbhub_ram_helper.py",)) in layout.calls
    assert layout.activated == [NEW]


def test_state_from_store_when_layout_does_not_know(store):
    store.save({**store.load(), "current": OLD})
    layout = FakeLayout(current=None)
    assert update_once(FakeGitHub(), store, layout, 0) == "updated"


def test_self_check_failure_deletes_folder(store):
    layout = FakeLayout(check_ok=False)
    update_once(FakeGitHub(), store, layout, 0)
    assert not store.version_dir(NEW).exists()


def test_unhealthy_rollback_deletes_failed_folder_and_clears_pending(store):
    update_once(FakeGitHub(), store, FakeLayout(healthy=False), 0)
    assert not store.version_dir(NEW).exists()
    assert store.version_dir(OLD).is_dir()
    assert store.load()["pending"] is None
    assert store.load()["current"] == OLD


def test_missing_old_folder_means_no_update(tmp_path):
    s = Store(tmp_path / "root")  # OLD is installed but has no folder to go back to
    gh, layout = FakeGitHub(), FakeLayout()
    assert update_once(gh, s, layout, 0) == "failed"
    assert layout.activated == [] and gh.downloads == []


def test_rollback_raising_still_notifies(store, caplog):
    class Stuck(FakeLayout):
        def activate(self, version):
            if version.name == OLD:
                raise OSError("disk gone")
            super().activate(version)

    layout = Stuck(healthy=False)
    with caplog.at_level(logging.WARNING, logger="rgbhub.updater"):
        assert update_once(FakeGitHub(), store, layout, 0) == "failed"
    assert len(layout.notes) == 1 and NEW[:7] in layout.notes[0]
    assert "disk gone" in caplog.text
    assert NEW in store.load()["bad"]
    assert store.version_dir(NEW).is_dir()  # may still be the active one: kept
    assert store.load()["pending"] == {"sha": NEW, "from": OLD}
    # next run tries going back again
    layout.activate = FakeLayout.activate.__get__(layout)
    assert update_once(FakeGitHub(), store, layout, 0) == "rolled-back"
    assert layout.current == OLD and not store.version_dir(NEW).exists()


def test_died_between_activate_and_health_check_rolls_back(store):
    store.version_dir(NEW).mkdir(parents=True)
    store.save({**store.load(), "current": OLD, "pending": {"sha": NEW, "from": OLD}})
    gh, layout = FakeGitHub(latest=NEW), FakeLayout(current=NEW)
    assert update_once(gh, store, layout, 0) == "rolled-back"
    assert layout.activated == [OLD]
    assert ("restart",) in layout.calls
    state = store.load()
    assert NEW in state["bad"] and state["pending"] is None and state["current"] == OLD
    assert not store.version_dir(NEW).exists()
    assert layout.notes == [f"RGB-Hub update {NEW[:7]} didn't start; went back to {OLD[:7]}"]
    # and the next run leaves it alone until main moves on
    assert update_once(gh, store, layout, 0) == "bad-commit"


def test_pending_that_never_activated_is_cleared(store):
    store.save({**store.load(), "current": OLD, "pending": {"sha": NEW, "from": OLD}})
    layout = FakeLayout(current=OLD)
    assert update_once(FakeGitHub(latest=OLD), store, layout, 0) == "up-to-date"
    assert store.load()["pending"] is None and store.load()["bad"] == []
    assert layout.activated == []


def test_bad_installed_commit_rolls_back_at_start(store):
    store.version_dir(NEW).mkdir(parents=True)
    store.save({**store.load(), "current": NEW, "previous": OLD, "bad": [NEW]})
    layout = FakeLayout(current=NEW)
    assert update_once(FakeGitHub(latest=NEW), store, layout, 0) == "rolled-back"
    assert layout.activated == [OLD]
    assert store.load()["current"] == OLD


def test_bad_installed_without_previous_folder_carries_on(store):
    store.save({**store.load(), "current": NEW, "previous": NEWER, "bad": [NEW]})
    layout = FakeLayout(current=NEW)
    assert update_once(FakeGitHub(latest=NEW), store, layout, 0) == "up-to-date"
    assert layout.activated == []


def test_overlapping_run_is_busy(store):
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "update.lock").write_text(str(os.getpid()))
    partial = store.versions / f".{NEW}.partial"
    partial.mkdir(parents=True)
    gh, layout = FakeGitHub(), FakeLayout()
    assert update_once(gh, store, layout, 0) == "busy"
    assert partial.exists() and gh.downloads == []
    assert (store.root / "update.lock").exists()


def test_stale_lock_from_dead_pid_is_taken(store, monkeypatch):
    import rgbhub.updater.store as store_mod

    monkeypatch.setattr(store_mod, "_IS_WINDOWS", False)
    (store.root / "update.lock").write_text("999999999")
    assert update_once(FakeGitHub(), store, FakeLayout(), 0) == "updated"
    assert not (store.root / "update.lock").exists()


def test_windows_lock_goes_stale_after_two_hours(store, monkeypatch):
    import rgbhub.updater.store as store_mod

    monkeypatch.setattr(store_mod, "_IS_WINDOWS", True)
    lock = store.root / "update.lock"
    lock.write_text(str(os.getpid()))
    assert update_once(FakeGitHub(), store, FakeLayout(), 0) == "busy"
    old = lock.stat().st_mtime - 3 * 3600
    os.utime(lock, (old, old))
    assert update_once(FakeGitHub(), store, FakeLayout(), 0) == "updated"


# ---- store --------------------------------------------------------------


def test_prune_keeps_current_and_previous(tmp_path):
    s = Store(tmp_path)
    shas = [f"{i:x}" * 40 for i in range(1, 7)]
    for age, sha in enumerate(shas):
        d = s.version_dir(sha)
        d.mkdir(parents=True)
        os.utime(d, (1000 + age, 1000 + age))
    # current and previous are the two oldest folders on purpose.
    s.save({**s.load(), "current": shas[0], "previous": shas[1]})
    s.prune(keep=3)
    left = sorted(p.name for p in s.versions.iterdir())
    assert left == sorted([shas[0], shas[1], shas[3], shas[4], shas[5]])


def test_state_write_is_atomic_and_tolerates_garbage(tmp_path):
    s = Store(tmp_path)
    s.state_path.write_text("{not json")
    assert s.load() == {
        "current": None,
        "previous": None,
        "bad": [],
        "checked": None,
        "pending": None,
    }
    s.mark_bad(NEW)
    assert json.loads(s.state_path.read_text())["bad"] == [NEW]
    assert [p.name for p in tmp_path.iterdir()] == ["update-state.json"]


def test_sha_checked_before_use_in_paths(tmp_path):
    s = Store(tmp_path)
    for bad in ["../x", "ABC" + "a" * 37, "a" * 39, "", "a" * 40 + "/"]:
        with pytest.raises(UpdateError):
            s.version_dir(bad)


def _unsafe_zips():
    def symlink_info():
        info = zipfile.ZipInfo("top/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        return info

    yield make_zip({}, extra=[(zipfile.ZipInfo("/etc/passwd"), "x")])
    yield make_zip({"../escape": "x"})
    yield make_zip({}, extra=[(zipfile.ZipInfo("top/../../escape"), "x")])
    yield make_zip({}, extra=[(zipfile.ZipInfo("C:/Windows/evil"), "x")])
    yield make_zip({}, extra=[(zipfile.ZipInfo("top\\..\\..\\evil"), "x")])
    yield make_zip({}, top="top", extra=[(symlink_info(), "/etc/passwd")])
    yield make_zip({}, top="top", extra=[(zipfile.ZipInfo("other/file"), "x")])


@pytest.mark.parametrize("data", list(_unsafe_zips()))
def test_unsafe_zip_refused(tmp_path, data):
    s = Store(tmp_path / "root")
    z = tmp_path / "in.zip"
    z.write_bytes(data)
    with pytest.raises(UpdateError):
        s.unpack(z, NEW)
    assert not s.version_dir(NEW).exists()
    assert list(s.versions.iterdir()) == []
    assert not (tmp_path / "escape").exists()


def test_unpack_keeps_exec_bit(tmp_path):
    info = zipfile.ZipInfo("top/install/setup.sh")
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    z = tmp_path / "in.zip"
    z.write_bytes(make_zip({}, top="top", extra=[(info, "#!/bin/sh\n")]))
    out = Store(tmp_path / "root").unpack(z, NEW)
    assert os.access(out / "install" / "setup.sh", os.X_OK)


# ---- github -------------------------------------------------------------


class Recorder:
    def __init__(self, body=b"{}"):
        self.body = body
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append((req, timeout))
        return io.BytesIO(self.body)


def test_github_requests():
    rec = Recorder(json.dumps({"sha": NEW}).encode())
    assert GitHub(opener=rec).latest() == NEW
    req, timeout = rec.requests[0]
    assert req.full_url == "https://api.github.com/repos/Konfus-dev/RGB-Hub/commits/main"
    assert req.get_header("User-agent") == "rgbhub-updater"
    assert timeout == 30

    rec.body = json.dumps({"status": "ahead"}).encode()
    assert GitHub(opener=rec).is_ahead(OLD, NEW) is True
    assert rec.requests[-1][0].full_url.endswith(f"/repos/Konfus-dev/RGB-Hub/compare/{OLD}...{NEW}")
    for status in ["behind", "identical", "diverged"]:
        rec.body = json.dumps({"status": status}).encode()
        assert GitHub(opener=rec).is_ahead(OLD, NEW) is False


def test_github_rejects_bad_sha():
    with pytest.raises(UpdateError):
        GitHub(opener=Recorder(json.dumps({"sha": "../../x"}).encode())).latest()
    with pytest.raises(UpdateError):
        GitHub(opener=Recorder(b"<html>")).latest()
    rec = Recorder()
    with pytest.raises(UpdateError):
        GitHub(opener=rec).is_ahead(OLD, "main")
    assert rec.requests == []


def test_github_download(tmp_path):
    rec = Recorder(b"zipdata")
    dest = tmp_path / "dl" / f"{NEW}.zip"
    GitHub(opener=rec).download(NEW, dest)
    assert dest.read_bytes() == b"zipdata"
    assert (
        rec.requests[0][0].full_url == f"https://codeload.github.com/Konfus-dev/RGB-Hub/zip/{NEW}"
    )
    assert not dest.with_suffix(".part").exists()


def test_github_download_error_leaves_no_file(tmp_path):
    class Dies(io.BytesIO):
        def read(self, *a):
            raise ConnectionResetError("cut")

    dest = tmp_path / f"{NEW}.zip"
    with pytest.raises(UpdateError):
        GitHub(opener=lambda req, timeout: Dies()).download(NEW, dest)
    assert list(tmp_path.iterdir()) == []


def test_json_answer_capped(monkeypatch):
    import rgbhub.updater.github as gh_mod

    monkeypatch.setattr(gh_mod, "MAX_JSON_BYTES", 100)
    body = json.dumps({"sha": NEW, "pad": "x" * 200}).encode()
    with pytest.raises(UpdateError):
        GitHub(opener=Recorder(body)).latest()


def test_download_capped(monkeypatch, tmp_path):
    import rgbhub.updater.github as gh_mod

    monkeypatch.setattr(gh_mod, "MAX_DOWNLOAD_BYTES", 1000)
    dest = tmp_path / f"{NEW}.zip"
    with pytest.raises(UpdateError):
        GitHub(opener=Recorder(b"z" * 1001)).download(NEW, dest)
    assert list(tmp_path.iterdir()) == []
    GitHub(opener=Recorder(b"z" * 1000)).download(NEW, dest)
    assert dest.stat().st_size == 1000


# ---- command line -------------------------------------------------------


def test_loop_never_raises_and_jitters(monkeypatch, tmp_path):
    calls, sleeps = [], []

    def fake_update_once(gh, store, layout, health_wait_s=60):
        calls.append(1)
        raise RuntimeError("boom")

    class Stop(BaseException):
        pass

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise Stop

    monkeypatch.setattr(updater_main, "update_once", fake_update_once)
    monkeypatch.setattr(updater_main, "load_layout", lambda platform, root: (FakeLayout(), root))
    with pytest.raises(Stop):
        updater_main.main(["--platform", "gnome", "--loop", "--root", str(tmp_path)], sleep=sleep)
    assert len(calls) == 3
    assert all(3300 <= s <= 3900 for s in sleeps)


def test_once(monkeypatch, tmp_path):
    monkeypatch.setattr(updater_main, "update_once", lambda *a, **k: "up-to-date")
    monkeypatch.setattr(updater_main, "load_layout", lambda platform, root: (FakeLayout(), root))
    assert updater_main.main(["--platform", "windows", "--once", "--root", str(tmp_path)]) == 0


def test_missing_layout_is_a_clear_error(monkeypatch, capsys, tmp_path):
    import importlib

    real = importlib.import_module

    def fake_import(name, *a):
        if name == "rgbhub.updater.layouts.batocera":
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real(name, *a)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    assert updater_main.main(["--platform", "batocera", "--root", str(tmp_path)]) == 2
    assert "batocera" in capsys.readouterr().err


# ---- stdlib only --------------------------------------------------------


def test_updater_imports_only_stdlib_and_itself():
    """Task 14 runs the updater from a copy of just this folder."""
    allowed = set(sys.stdlib_module_names)
    files = sorted(UPDATER_DIR.rglob("*.py"))
    assert files
    for path in files:
        package = ["rgbhub", *path.parent.relative_to(UPDATER_DIR.parent).parts]
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - node.level + 1]
                    names = [".".join(base + ([node.module] if node.module else []))]
                else:
                    names = [node.module]
            else:
                continue
            for name in names:
                ok = (
                    name.split(".")[0] in allowed
                    or name == "rgbhub.updater"
                    or (name.startswith("rgbhub.updater."))
                )
                assert ok, f"{path.name} imports {name}"


def test_check_only_loads_the_layout(monkeypatch, capsys, tmp_path):
    def no_update(*a, **k):
        raise AssertionError("--check must not run an update")

    monkeypatch.setattr(updater_main, "update_once", no_update)
    monkeypatch.setattr(updater_main, "GitHub", no_update)
    for platform in ("gnome", "batocera"):
        assert updater_main.main(["--platform", platform, "--check", "--root", str(tmp_path)]) == 0
    assert "ok" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_paused_layout_means_no_work_at_all(store):
    class Paused(FakeLayout):
        def paused(self):
            return True

    class NoNetwork(FakeGitHub):
        def latest(self, branch="main"):
            raise AssertionError("no network while paused")

    layout = Paused()
    assert update_once(NoNetwork(), store, layout, health_wait_s=0) == "paused"
    assert layout.calls == [] and layout.activated == []
