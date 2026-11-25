# SPDX-License-Identifier: GPL-3.0-or-later
"""The few GitHub calls the updater needs. Sends nothing but a fixed User-Agent."""

import http.client
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from rgbhub.updater import UpdateError, check_sha

REPO = "Konfus-dev/RGB-Hub"
API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"
USER_AGENT = "rgbhub-updater"
TIMEOUT_S = 30
_REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class GitHub:
    def __init__(self, repo: str = REPO, opener=urllib.request.urlopen):
        if not _REPO_NAME.fullmatch(repo):
            raise ValueError(f"not a GitHub repo name: {repo!r}")
        self.repo = repo
        self.opener = opener

    def _open(self, url: str, accept: str):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            return self.opener(request, timeout=TIMEOUT_S)
        except urllib.error.HTTPError as e:
            e.close()
            raise UpdateError(f"GitHub answered {e.code} for {url}", status=e.code) from e
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise UpdateError(f"could not reach GitHub ({url}): {e}") from e

    def _json(self, path: str) -> dict:
        url = f"{API}/repos/{self.repo}/{path}"
        response = self._open(url, "application/vnd.github+json")
        try:
            with response:
                data = json.load(response)
        except (OSError, ValueError, http.client.HTTPException) as e:
            raise UpdateError(f"bad answer from GitHub for {url}: {e}") from e
        if not isinstance(data, dict):
            raise UpdateError(f"bad answer from GitHub for {url}")
        return data

    def latest(self, branch: str = "main") -> str:
        """The commit id at the tip of `branch`."""
        data = self._json(f"commits/{urllib.parse.quote(branch, safe='')}")
        return check_sha(data.get("sha"))

    def is_ahead(self, base: str, head: str) -> bool:
        """True only if `head` is a descendant of `base` (moving forward).

        404 (GitHub doesn't know `base`, e.g. a local-only commit) is False:
        we can't prove the new commit is newer, so we don't take it."""
        check_sha(base)
        check_sha(head)
        try:
            data = self._json(f"compare/{base}...{head}")
        except UpdateError as e:
            if e.status == 404:
                return False
            raise
        return data.get("status") == "ahead"

    def download(self, sha: str, dest: Path) -> None:
        """Save the zip of commit `sha` to `dest` (via `dest.part`, renamed when complete)."""
        check_sha(sha)
        dest = Path(dest)
        part = dest.with_suffix(".part")
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"{CODELOAD}/{self.repo}/zip/{sha}"
        try:
            with self._open(url, "application/zip") as response, open(part, "wb") as out:
                shutil.copyfileobj(response, out)
            os.replace(part, dest)
        except UpdateError:
            part.unlink(missing_ok=True)
            raise
        except (OSError, http.client.HTTPException) as e:
            part.unlink(missing_ok=True)
            raise UpdateError(f"download of {sha[:7]} failed: {e}") from e
