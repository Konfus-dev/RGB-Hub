# SPDX-License-Identifier: GPL-3.0-or-later
"""Where versions live: `root/versions/<sha>/`, one folder each, plus the update state.

A version folder only ever appears under its final name once it is complete:
it is unpacked into `versions/.<sha>.partial` and renamed at the end, so a
power cut mid-unpack leaves only a partial folder, which the next run deletes.
"""

import json
import logging
import os
import shutil
import stat
import time
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from rgbhub.updater import UpdateError, check_sha, is_sha

log = logging.getLogger("rgbhub.updater")

MAX_UNPACKED_BYTES = 500 * 1024 * 1024  # the repo is a few MB; refuse zip bombs
MAX_BAD = 50
LOCK_STALE_S = 2 * 3600  # Windows: a lock this old is left over from a crash
_IS_WINDOWS = os.name == "nt"


def _empty_state() -> dict:
    # pending: {"sha", "from"} while a new version is active but not yet seen healthy.
    return {"current": None, "previous": None, "bad": [], "checked": None, "pending": None}


def _remove(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("could not remove %s: %s", path, e)


def _safe_parts(name: str) -> list[str]:
    """The path parts of a zip member, or UpdateError if it could land outside the folder."""
    name = name.replace("\\", "/")
    parts = name.split("/")
    if (
        name.startswith("/")
        or ".." in parts
        or any(":" in p for p in parts)  # drive letters, Windows alternate streams
        or "\0" in name
    ):
        raise UpdateError(f"unsafe path in update zip: {name[:100]!r}")
    return parts


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.versions = self.root / "versions"
        self.downloads = self.root / "downloads"
        self.state_path = self.root / "update-state.json"

    # ---- state ------------------------------------------------------------

    def load(self) -> dict:
        """The update state; a missing or damaged file reads as empty."""
        state = _empty_state()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s: %s", self.state_path, e)
            return state
        if not isinstance(data, dict):
            return state
        for key in ("current", "previous"):
            if is_sha(data.get(key)):
                state[key] = data[key]
        if isinstance(data.get("bad"), list):
            state["bad"] = [s for s in data["bad"] if is_sha(s)]
        if isinstance(data.get("checked"), str):
            state["checked"] = data["checked"]
        pending = data.get("pending")
        if isinstance(pending, dict) and is_sha(pending.get("sha")) and is_sha(pending.get("from")):
            state["pending"] = {"sha": pending["sha"], "from": pending["from"]}
        return state

    def save(self, state: dict) -> None:
        """Write the state atomically (temporary file, then replace)."""
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f".{self.state_path.name}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    def mark_checked(self) -> None:
        state = self.load()
        state["checked"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.save(state)

    def mark_bad(self, sha: str) -> None:
        check_sha(sha)
        state = self.load()
        if sha not in state["bad"]:
            state["bad"] = (state["bad"] + [sha])[-MAX_BAD:]
        self.save(state)

    def set_pending(self, sha: str, from_sha: str) -> None:
        """Record that `sha` is about to be activated and has not passed its health check."""
        state = self.load()
        state["pending"] = {"sha": check_sha(sha), "from": check_sha(from_sha)}
        self.save(state)

    def clear_pending(self) -> None:
        state = self.load()
        if state["pending"] is not None:
            state["pending"] = None
            self.save(state)

    def set_current(self, sha: str, previous: str | None) -> None:
        state = self.load()
        state["current"] = check_sha(sha)
        state["previous"] = previous if is_sha(previous) else None
        state["pending"] = None
        state["checked"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.save(state)

    # ---- version folders ----------------------------------------------------

    def version_dir(self, sha: str) -> Path:
        return self.versions / check_sha(sha)

    def remove_version(self, sha: str) -> None:
        """Delete a version folder (a failed one); logged, never raises."""
        path = self.version_dir(sha)
        if path.exists():
            log.info("removing failed version %s", sha[:7])
            _remove(path)

    # ---- one run at a time ----------------------------------------------------

    @contextmanager
    def locked(self):
        """Yield True while holding `update.lock`, False if another run holds it.

        The lock holds the owner's pid. It is stale when that process is gone
        (POSIX) or, on Windows where that is awkward to check without extra
        modules, when it is older than two hours."""
        lock = self.root / "update.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        got = self._try_lock(lock) or (self._lock_is_stale(lock) and self._retake(lock))
        try:
            yield got
        finally:
            if got:
                lock.unlink(missing_ok=True)

    @staticmethod
    def _try_lock(lock: Path) -> bool:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True

    def _retake(self, lock: Path) -> bool:
        log.info("removing stale %s", lock.name)
        lock.unlink(missing_ok=True)
        return self._try_lock(lock)

    @staticmethod
    def _lock_is_stale(lock: Path) -> bool:
        try:
            age = time.time() - lock.stat().st_mtime
            text = lock.read_text(encoding="ascii", errors="replace").strip()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if _IS_WINDOWS or not text.isdigit():
            return age > LOCK_STALE_S
        try:
            os.kill(int(text), 0)
        except (ProcessLookupError, OverflowError):
            return True  # no such process (or not a possible pid)
        except PermissionError:
            return False  # alive, owned by someone else
        return False

    def zip_path(self, sha: str) -> Path:
        return self.downloads / f"{check_sha(sha)}.zip"

    def clean_partials(self) -> None:
        """Delete what an interrupted run left: half-unpacked versions and downloads."""
        if self.versions.is_dir():
            for p in self.versions.iterdir():
                if p.name.startswith(".") and p.name.endswith((".partial", ".old")):
                    log.info("removing unfinished %s", p.name)
                    _remove(p)
        if self.downloads.is_dir():
            for p in self.downloads.iterdir():
                _remove(p)

    def unpack(self, zip_path: Path, sha: str) -> Path:
        """Unpack the GitHub zip of `sha` into `versions/<sha>/` and return that folder.

        The zip is untrusted: absolute paths, `..`, drive letters, symlinks or
        more than one top folder are refused. The zip's single top folder is
        stripped."""
        final = self.version_dir(sha)
        partial = self.versions / f".{sha}.partial"
        self.versions.mkdir(parents=True, exist_ok=True)
        _remove(partial)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                _extract(zf, partial)
            if final.exists():
                # Left from an earlier attempt that failed later on; never the active one,
                # since the flow only unpacks commits that are not installed.
                old = self.versions / f".{sha}.old"
                _remove(old)
                os.rename(final, old)
                _remove(old)
            os.rename(partial, final)
        except UpdateError:
            _remove(partial)
            raise
        except (OSError, zipfile.BadZipFile, EOFError, RuntimeError, ValueError) as e:
            _remove(partial)
            raise UpdateError(f"could not unpack update {sha[:7]}: {e}") from e
        return final

    def prune(self, keep: int = 3) -> None:
        """Keep the `keep` newest versions, and always the current and previous one."""
        if not self.versions.is_dir():
            return
        state = self.load()
        folders = [p for p in self.versions.iterdir() if p.is_dir() and is_sha(p.name)]
        folders.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        protected = {state["current"], state["previous"]}
        for p in folders[keep:]:
            if p.name not in protected:
                log.info("removing old version %s", p.name[:7])
                _remove(p)


def _extract(zf: zipfile.ZipFile, dest: Path) -> None:
    top = None
    total = 0
    dest.mkdir(parents=True)
    for info in zf.infolist():
        parts = _safe_parts(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise UpdateError(f"symlink in update zip: {info.filename[:100]!r}")
        if top is None:
            top = parts[0]
        if not top or parts[0] != top:
            raise UpdateError("update zip does not have a single top folder")
        rel = [p for p in parts[1:] if p not in ("", ".")]
        if not rel:
            continue
        target = dest.joinpath(*rel)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise UpdateError("update zip is too big")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        if mode & 0o111:
            target.chmod(0o755)
