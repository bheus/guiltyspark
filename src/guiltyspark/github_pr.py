"""Live disposition of the pull requests guiltyspark has opened.

The remediation ``status`` stored in SQLite records what guiltyspark *did*
(``pr-opened``, ``validated``, ``failed``); it never changes once written. To
tell an operator whether a corrective measure was ultimately integrated,
dismissed, or is still awaiting review, we have to ask GitHub for the PR's
current state. Reads go through the same authenticated client used to open the
PR, so private repositories resolve too.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from guiltyspark.config import Settings
from guiltyspark.github_auth import GitHubAuth

_PR_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")

# Dispositions surfaced to the dashboard.
MERGED = "merged"
CLOSED = "closed"
DRAFT = "draft"
OPEN = "open"
UNKNOWN = "unknown"


def parse_pr_url(url: str | None) -> tuple[str, str, int] | None:
    """Return ``(owner, repo, number)`` for a GitHub PR URL, or ``None``."""
    match = _PR_URL.search(url or "")
    if not match:
        return None
    return match.group(1), match.group(2), int(match.group(3))


def classify_pr(payload: dict) -> str:
    """Map a GitHub pull-request payload to a single disposition."""
    if payload.get("merged_at") or payload.get("merged"):
        return MERGED
    if payload.get("state") == "closed":
        return CLOSED
    if payload.get("draft"):
        return DRAFT
    return OPEN


class PrStatusClient:
    """Fetches PR dispositions with a short TTL cache.

    The dashboard re-polls on a fixed cadence; the cache keeps that from turning
    into a GitHub API call per PR per poll. Failures (missing auth, deleted PR,
    no access to a private repo) resolve to ``unknown`` rather than raising, so a
    single unreachable PR never breaks the remediations view.
    """

    def __init__(
        self,
        settings: Settings,
        auth: GitHubAuth | None = None,
        ttl_seconds: float = 120.0,
    ) -> None:
        self.settings = settings
        self.auth = auth or GitHubAuth(settings)
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, dict]] = {}

    def status(self, pr_url: str | None) -> dict | None:
        """Return ``{"state", "merged_at", "closed_at", "html_url"}`` or ``None``.

        ``None`` means the URL is not a recognizable GitHub PR (nothing to show).
        A reachable-but-failed lookup returns a dict with ``state == "unknown"``.
        """
        parsed = parse_pr_url(pr_url)
        if parsed is None or pr_url is None:
            return None
        now = time.time()
        cached = self._cache.get(pr_url)
        if cached is not None and now - cached[0] < self.ttl_seconds:
            return cached[1]
        result = self._fetch(*parsed)
        self._cache[pr_url] = (now, result)
        return result

    def _fetch(self, owner: str, repo: str, number: int) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            token = self.auth.token(required=False)
        except Exception:
            token = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.settings.github_api_url}/repos/{owner}/{repo}/pulls/{number}",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return {"state": UNKNOWN, "merged_at": None, "closed_at": None}
        return {
            "state": classify_pr(payload),
            "merged_at": payload.get("merged_at"),
            "closed_at": payload.get("closed_at"),
        }
