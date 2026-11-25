# SPDX-License-Identifier: GPL-3.0-or-later
"""One update check: is there a newer `main`? If so install, switch, verify, or roll back.

There is always a version to go back to: a new one is only activated while the
installed one's folder exists, and an activation is recorded as pending until
the restarted hub is seen healthy. A run that finds the installed commit bad,
or a pending activation that never finished (power cut, crash), goes back first.
"""

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
    """Run one check. Returns "paused", "up-to-date", "not-ahead", "busy", "bad-commit",
    "updated", "rolled-back" or "failed".

    "paused": the layout says updating is switched off (`paused()`, optional;
    Ubuntu's `install-user.sh --dev`); nothing is read, fetched or changed.

    Network and download problems log one line and return "failed" with the
    current version and the state file untouched; the next run tries again.
    Another run already in progress means "busy" and nothing is touched."""
    paused = getattr(layout, "paused", None)
    if paused is not None and paused():
        log.info("updates are paused")
        return "paused"
    try:
        with store.locked() as got:
            if not got:
                log.info("another update run is in progress")
                return "busy"
            return _update_once(gh, store, layout, health_wait_s)
    except UpdateError as e:
        log.warning("update check failed: %s", e)
        return "failed"


def _notify(layout, text: str) -> None:
    try:
        layout.notify(text)
    except Exception as e:
        log.warning("could not show notification: %s", e)


def _roll_back(store, layout, bad: str, target: str) -> str:
    """Mark `bad` bad and switch back to `target`; the owner is told either way."""
    store.mark_bad(bad)
    try:
        layout.activate(store.version_dir(target))
        layout.restart_hub()
    except Exception as e:
        # Pending stays recorded, so the next run tries going back again.
        log.warning("going back to %s failed: %s", short(target), e)
        text = (
            f"RGB-Hub update {short(bad)} didn't start, and going back to "
            f"{short(target)} failed too; see the log"
        )
        log.warning(text)
        _notify(layout, text)
        return "failed"
    state = store.load()
    previous = None if state["previous"] in (target, bad) else state["previous"]
    store.set_current(target, previous=previous)  # also clears pending
    store.remove_version(bad)
    text = f"RGB-Hub update {short(bad)} didn't start; went back to {short(target)}"
    log.warning(text)
    _notify(layout, text)
    return "rolled-back"


def _recover(store, layout, current: str | None) -> str | None:
    """Go back if the installed commit is known bad or never passed its health check.

    Returns "rolled-back"/"failed" if it acted, None to carry on with a normal check."""
    state = store.load()
    pending = state["pending"]
    if pending is not None and pending["sha"] != current:
        store.clear_pending()  # died before the switch: nothing was activated
        pending = None
    if current is None:
        return None
    if pending is not None:
        target, why = pending["from"], "never passed its health check"
    elif current in state["bad"]:
        target, why = state["previous"] if state["current"] == current else None, "is marked bad"
    else:
        return None
    if not is_sha(target) or target == current or not store.version_dir(target).is_dir():
        log.warning("installed %s %s, but there is no version to go back to", short(current), why)
        store.mark_bad(current)
        store.clear_pending()
        return None
    log.warning("installed %s %s; going back to %s", short(current), why, short(target))
    return _roll_back(store, layout, current, target)


def _update_once(gh, store, layout, health_wait_s: int) -> str:
    store.clean_partials()
    current = _installed(store, layout)
    recovered = _recover(store, layout, current)
    if recovered is not None:
        return recovered

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
    old = store.version_dir(current)
    if not old.is_dir():
        raise UpdateError(f"no folder for installed {short(current)} to go back to; not updating")

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
        store.remove_version(latest)
        log.warning("update %s failed its self-check; skipping it", short(latest))
        return "failed"

    changes = layout.privileged_changes(old, new)
    if changes:
        log.info("update %s needs approval for: %s", short(latest), ", ".join(changes))
        layout.request_privileged(new, changes)

    store.set_pending(latest, current)
    try:
        layout.activate(new)
        layout.restart_hub()
        healthy = layout.hub_healthy(health_wait_s)
    except Exception as e:  # anything going wrong while switching means: go back
        log.warning("switching to %s failed: %s", short(latest), e)
        healthy = False
    if not healthy:
        return _roll_back(store, layout, latest, current)

    store.set_current(latest, previous=current)  # also clears pending
    store.prune(keep=3)
    text = f"RGB-Hub updated to {short(latest)}"
    log.info(text)
    _notify(layout, text)
    return "updated"
