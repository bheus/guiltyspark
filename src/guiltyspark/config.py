from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_files() -> None:
    original_keys = set(os.environ)
    for path in (Path(".env"), Path(".env.local")):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key in original_keys:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    loki_url: str
    loki_query: str
    loki_limit: int
    interval_seconds: int
    lookback_seconds: int
    state_path: Path
    findings_path: Path
    min_events: int
    max_incidents_per_run: int
    model: str | None
    runbook_path: Path | None
    notify_webhook_url: str | None
    codex_workdir: Path
    codex_home: Path
    codex_path: str
    codex_timeout_seconds: int
    pr_mode: str
    loki_bearer_token: str | None = None
    loki_basic_auth: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_files()
        pr_mode = os.getenv("GUILTYSPARK_PR_MODE", "off").strip().lower()
        if pr_mode not in {"off", "plan", "branch"}:
            raise ValueError("GUILTYSPARK_PR_MODE must be one of: off, plan, branch")

        return cls(
            loki_url=os.getenv("LOKI_URL", "http://localhost:3100").rstrip("/"),
            loki_query=os.getenv("LOKI_QUERY", '{job=~".+"}'),
            loki_limit=_int("LOKI_LIMIT", 5000),
            interval_seconds=_int("GUILTYSPARK_INTERVAL_SECONDS", 300),
            lookback_seconds=_int("GUILTYSPARK_LOOKBACK_SECONDS", 900),
            state_path=Path(os.getenv("GUILTYSPARK_STATE_PATH", "data/guiltyspark.sqlite3")),
            findings_path=Path(os.getenv("GUILTYSPARK_FINDINGS_PATH", "data/findings.jsonl")),
            min_events=_int("GUILTYSPARK_MIN_EVENTS", 2),
            max_incidents_per_run=_int("GUILTYSPARK_MAX_INCIDENTS_PER_RUN", 8),
            model=os.getenv("GUILTYSPARK_MODEL") or None,
            runbook_path=Path(os.getenv("GUILTYSPARK_RUNBOOK_PATH", "knowledge/homelab-runbook.md")),
            notify_webhook_url=os.getenv("GUILTYSPARK_NOTIFY_WEBHOOK_URL") or None,
            codex_workdir=Path(os.getenv("GUILTYSPARK_CODEX_WORKDIR", "/app")),
            codex_home=Path(os.getenv("CODEX_HOME", "/data/codex")),
            codex_path=os.getenv("GUILTYSPARK_CODEX_PATH", "codex"),
            codex_timeout_seconds=_int("GUILTYSPARK_CODEX_TIMEOUT_SECONDS", 600),
            pr_mode=pr_mode,
            loki_bearer_token=os.getenv("LOKI_BEARER_TOKEN") or None,
            loki_basic_auth=os.getenv("LOKI_BASIC_AUTH") or None,
        )
