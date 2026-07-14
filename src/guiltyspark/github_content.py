"""Fetch a repository's documented "expected logs" so analysis can discount them.

Some log lines a service emits are known-benign — startup chatter, deliberate
warnings, ret/retry notices — and a repository can document them (Abraham keeps
them in ``docs/EXPECTED_LOGS.md``). When a target names such a file, the Monitor
fetches its contents from GitHub and hands them to Codex as context, so those
patterns are not mistaken for anomalies.

Reads go through the same authenticated client used to open PRs, so private
repositories resolve too. Every failure — no auth, missing file, unreachable
API — resolves to ``None`` rather than raising: a missing doc simply means no
extra context, never a stalled analysis cycle.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request

from guiltyspark.config import Settings
from guiltyspark.github_auth import GitHubAuth
from guiltyspark.targets import Target

# Guard the prompt against an oversized doc; matches the runbook cap in agent.py.
_MAX_CHARS = 16_000


class RepoDocClient:
    """Fetches a repo-relative text document with a TTL cache.

    Analysis runs on a fixed cadence and the documentation changes rarely, so a
    per-instance cache keeps that from becoming a GitHub call every cycle.
    """

    def __init__(
        self,
        settings: Settings,
        auth: GitHubAuth | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        self.settings = settings
        self.auth = auth or GitHubAuth(settings)
        self.ttl_seconds = (
            settings.expected_logs_cache_seconds if ttl_seconds is None else ttl_seconds
        )
        self._cache: dict[str, tuple[float, str | None]] = {}

    def expected_logs(self, target: Target | None) -> str | None:
        """Return the target's documented expected-logs text, or ``None``.

        ``None`` covers every non-result: no target, no configured path, or a
        fetch that failed. Callers treat it as "no extra context."
        """
        if target is None or not target.expected_logs_path:
            return None
        key = f"{target.github_repo}@{target.base_branch}:{target.expected_logs_path}"
        now = time.time()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < self.ttl_seconds:
            return cached[1]
        result = self._fetch(
            target.github_repo, target.base_branch, target.expected_logs_path
        )
        self._cache[key] = (now, result)
        return result

    def _fetch(self, repo: str, ref: str, path: str) -> str | None:
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
        url = f"{self.settings.github_api_url}/repos/{repo}/contents/{path}"
        if ref:
            url += f"?ref={ref}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            return None
        text = _decode_content(payload)
        if not text:
            return None
        return text[:_MAX_CHARS]


def _decode_content(payload: dict) -> str | None:
    """Decode a GitHub contents API payload into UTF-8 text, or ``None``.

    Only base64-encoded file blobs are supported; a directory listing (a JSON
    array) or an unexpected encoding yields ``None``.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("encoding") != "base64":
        return None
    raw = payload.get("content")
    if not isinstance(raw, str):
        return None
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return None
