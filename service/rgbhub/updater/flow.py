# SPDX-License-Identifier: GPL-3.0-or-later
"""One update check: is there a newer `main`? If so install, switch, verify, or roll back."""

import logging

from rgbhub.updater import UpdateError, is_sha, short

log = logging.getLogger("rgbhub.updater")


def _installed(store, layout) -> str | None:
    """The active commit: what the layout says is running, else what we last recorded."""
    sha = layout.current_commit()
    if is_sha(sha):
        return sha
    return store.load()["current"]


def update_once(gh, store, layout, health_wait_s: int = 60) -> str:
    """Run one check. Returns "up-to-date", "not-ahead", "busy", "bad-commit",
    "updated", "rolled-back" or "failed".

    Network and download problems log one line and return "failed" with the
    current version and the state file untouched; the next run tries again."""
    try:
        return _update_once(gh, store, layout, health_wait_s)
    except UpdateError as e:
        log.warning("update check failed: %s", e)
        return "failed"


def _update_once(gh, store, layout, health_wait_s: int) -> str:
    store.clean_partials()
    current = _installed(store, layout)
    latest = gh.latest("main")

    if latest == current:
        store.mark_checked()
        return "up-to-date"
    if latest in store.load()["bad"]:
        store.mark_checked()
        return "bad-commit"
    if current is None:
        raise UpdateError("installed commit unknown; not updating (can't tell newer from older)")
    if not gh.is_ahead(current, latest):
        log.info("main (%s) is not ahead of installed %s", short(latest), short(current))
        store.mark_checked()
        return "not-ahead"
    if layout.busy():
        log.info("update %s waits: busy", short(latest))
        return "busy"

    log.info("updating %s -> %s", short(current), short(latest))
    zip_path = store.zip_path(latest)
    try:
        gh.download(latest, zip_path)
        new = store.unpack(zip_path, latest)
    finally:
        zip_path.unlink(missing_ok=True)

    layout.prepare(new)
    if not layout.self_check(new):
        store.mark_bad(latest)
        log.warning("update %s failed its self-check; skipping it", short(latest))
        return "failed"

    old = store.version_dir(current)
    changes = layout.privileged_changes(old if old.is_dir() else None, new)
    if changes:
        log.info("update %s needs approval for: %s", short(latest), ", ".join(changes))
        layout.request_privileged(new, changes)

    try:
        layout.activate(new)
        layout.restart_hub()
        healthy = layout.hub_healthy(health_wait_s)
    except Exception as e:  # anything going wrong while switching means: go back
        log.warning("switching to %s failed: %s", short(latest), e)
        healthy = False

    if not healthy:
        store.mark_bad(latest)
        layout.activate(old)
        layout.restart_hub()
        text = f"RGB-Hub update {short(latest)} didn't start; went back to {short(current)}"
        log.warning(text)
        layout.notify(text)
        return "rolled-back"

    store.set_current(latest, previous=current)
    store.prune(keep=3)
    text = f"RGB-Hub updated to {short(latest)}"
    log.info(text)
    layout.notify(text)
    return "updated"
