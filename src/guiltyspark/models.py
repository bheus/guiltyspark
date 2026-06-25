from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


NOISE_WORDS = re.compile(
    r"([a-f0-9]{8,}|[0-9a-f]{4,}-[0-9a-f-]{12,}|\b\d+\b|0x[a-f0-9]+)",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class LogEvent:
    ts_ns: int
    labels: dict[str, str]
    line: str

    @property
    def timestamp(self) -> datetime:
        return datetime.fromtimestamp(self.ts_ns / 1_000_000_000, tz=timezone.utc)

    @property
    def service(self) -> str:
        for key in ("app", "service", "container", "container_name", "job", "namespace"):
            value = self.labels.get(key)
            if value:
                return value
        return "unknown"

    @property
    def level(self) -> str:
        explicit = self.labels.get("level") or self.labels.get("severity")
        if explicit:
            return explicit.lower()
        lowered = self.line.lower()
        if "panic" in lowered or "fatal" in lowered:
            return "fatal"
        if "error" in lowered or "exception" in lowered or "traceback" in lowered:
            return "error"
        if "warn" in lowered:
            return "warning"
        return "info"


@dataclass
class Incident:
    fingerprint: str
    service: str
    level: str
    first_seen_ns: int
    last_seen_ns: int
    count: int
    labels: dict[str, str]
    samples: list[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        sample_text = "\n".join(f"- {line}" for line in self.samples[:8])
        labels = json.dumps(self.labels, sort_keys=True)
        return (
            f"fingerprint: {self.fingerprint}\n"
            f"service: {self.service}\n"
            f"level: {self.level}\n"
            f"count: {self.count}\n"
            f"first_seen_utc: {self.first_seen_ns}\n"
            f"last_seen_utc: {self.last_seen_ns}\n"
            f"labels: {labels}\n"
            f"samples:\n{sample_text}"
        )


@dataclass(frozen=True)
class Finding:
    fingerprint: str
    title: str
    severity: str
    summary: str
    evidence: list[str]
    suspected_cause: str
    recommended_fix: str
    pr_recommended: bool
    raw: dict[str, Any]

    def stable_hash(self) -> str:
        content = json.dumps(
            {
                "fingerprint": self.fingerprint,
                "title": self.title,
                "severity": self.severity,
                "suspected_cause": self.suspected_cause,
            },
            sort_keys=True,
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def as_json_line(self) -> str:
        payload = asdict(self)
        payload["finding_hash"] = self.stable_hash()
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        return json.dumps(payload, sort_keys=True)


def normalize_line(line: str) -> str:
    normalized = NOISE_WORDS.sub("<var>", line.strip().lower())
    return WHITESPACE.sub(" ", normalized)


def fingerprint_for(event: LogEvent) -> str:
    content = {
        "service": event.service,
        "level": event.level,
        "line": normalize_line(event.line),
    }
    raw = json.dumps(content, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
