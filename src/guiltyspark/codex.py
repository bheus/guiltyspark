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
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from guiltyspark.config import Settings

_SECRET_MARKERS = ("AUTH", "CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN", "WEBHOOK")


def codex_exec(settings: Settings, prompt: str) -> str:
    """Run `codex exec` with `prompt` on stdin; return its last message text.

    Always runs read-only — the callers here only ever ask Codex to reason about
    log text, never to modify the workspace. Raises RuntimeError on a non-zero
    exit so callers can decide whether to surface or degrade.
    """
    if not settings.codex_workdir.exists():
        raise RuntimeError(
            f"GUILTYSPARK_CODEX_WORKDIR does not exist: {settings.codex_workdir}"
        )

    with tempfile.NamedTemporaryFile(
        "w+", encoding="utf-8", suffix=".txt", delete=False
    ) as output:
        output_path = Path(output.name)

    command = [
        settings.codex_path,
        "exec",
        "--cd",
        str(settings.codex_workdir),
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
    ]
    if settings.model:
        command.extend(["--model", settings.model])
    command.append("-")

    env = os.environ.copy()
    for name in list(env):
        if name == settings.github_token_env or any(
            marker in name.upper() for marker in _SECRET_MARKERS
        ):
            env.pop(name, None)
    env["CODEX_HOME"] = str(settings.codex_home)

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=settings.codex_timeout_seconds,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"codex exec failed with exit {completed.returncode}: {details}"
            )
        return output_path.read_text(encoding="utf-8")
    finally:
        output_path.unlink(missing_ok=True)


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
