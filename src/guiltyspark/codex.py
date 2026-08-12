"""Shared helpers for invoking `codex exec` and parsing its JSON replies.

Both the analysis agent (`agent.py`) and the dashboard's live anomaly
clustering (`clustering.py`) drive Codex the same way: pipe a prompt on stdin,
read the last assistant message from a temp file, and extract a JSON object from
it. Keeping the subprocess call and the secret-scrubbing in one place means those
two callers cannot drift apart on sandbox mode or env hygiene.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from guiltyspark.config import Settings

_SECRET_MARKERS = ("AUTH", "CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN", "WEBHOOK")

_TRANSPORT_LOCK = threading.Lock()
_transport_ready = True
_transport_error: str | None = None
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0


def transport_readiness() -> dict[str, object]:
    """Return the Codex transport's current readiness state."""
    with _TRANSPORT_LOCK:
        return {
            "ready": _transport_ready,
            "degraded": not _transport_ready,
            "error": _transport_error,
        }


def _is_transient_transport_failure(details: str) -> bool:
    lowered = details.lower()
    return (
        "http 502" in lowered
        or "http 503" in lowered
        or "http 504" in lowered
        or "http 429" in lowered
        or "circuit open" in lowered
        or "circuit_open" in lowered
        or "transport channel closed" in lowered
        or "error sending request" in lowered
        or "connection refused" in lowered
        or "connection reset" in lowered
        or "timed out" in lowered
        or "network is unreachable" in lowered
    )


def execute_codex(settings: Settings, prompt: str, *, sandbox: str = "read-only") -> str:
    """Run Codex, retrying transient upstream transport failures."""
    global _transport_ready, _transport_error

    if not settings.codex_workdir.exists():
        raise RuntimeError(
            f"GUILTYSPARK_CODEX_WORKDIR does not exist: {settings.codex_workdir}"
        )
    with tempfile.NamedTemporaryFile(
        "w+", encoding="utf-8", suffix=".txt", delete=False
    ) as output:
        output_path = Path(output.name)

    command = [
        settings.codex_path, "exec", "--cd", str(settings.codex_workdir),
        "--sandbox", sandbox, "--skip-git-repo-check", "--output-last-message",
        str(output_path),
    ]
    if settings.analysis_model_name:
        command.extend(["--model", settings.analysis_model_name])
    command.append("-")

    env = os.environ.copy()
    for name in list(env):
        if name == settings.github_token_env or any(
            marker in name.upper() for marker in _SECRET_MARKERS
        ):
            env.pop(name, None)
    env["CODEX_HOME"] = str(settings.codex_home)

    try:
        # Serialize the whole retry sequence so callers cannot create a
        # concurrent reconnect storm against the shared Codex transport.
        with _TRANSPORT_LOCK:
            for attempt in range(_MAX_ATTEMPTS):
                completed = subprocess.run(
                    command, input=prompt, text=True, capture_output=True,
                    timeout=settings.codex_timeout_seconds, check=False, env=env,
                )
                if completed.returncode == 0:
                    _transport_ready = True
                    _transport_error = None
                    return output_path.read_text(encoding="utf-8")

                details = f"{completed.stderr}\n{completed.stdout}".strip()
                _transport_ready = False
                _transport_error = details
                if (
                    not _is_transient_transport_failure(details)
                    or attempt == _MAX_ATTEMPTS - 1
                ):
                    raise RuntimeError(
                        f"codex exec failed with exit {completed.returncode}: {details}"
                    )
                delay = min(
                    _BACKOFF_CAP_SECONDS,
                    _BACKOFF_BASE_SECONDS * (2**attempt),
                )
                # Keep jitter within the lower half of each exponential step so
                # a later retry cannot randomly happen sooner than its predecessor.
                time.sleep(random.uniform(delay / 2, delay))
    finally:
        output_path.unlink(missing_ok=True)


def codex_exec(settings: Settings, prompt: str) -> str:
    """Run `codex exec` with `prompt` on stdin; return its last message text.

    Always runs read-only — the callers here only ever ask Codex to reason about
    log text, never to modify the workspace. Raises RuntimeError on a non-zero
    exit so callers can decide whether to surface or degrade.
    """
    return execute_codex(settings, prompt)


def extract_json(text: str) -> dict[str, Any]:
    """Pull a single JSON object out of Codex output, tolerating fences/prose."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1:
            raise RuntimeError(f"Codex returned non-JSON output: {text[:300]}")
        stripped = stripped[start : end + 1]
    return json.loads(stripped)
