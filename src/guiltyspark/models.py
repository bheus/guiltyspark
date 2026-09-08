from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# Timestamps must be collapsed as whole tokens, before the generic number pass
# below — which cannot do it. In `2026-07-25t13:00:23` the `T` separator is a word
# character, so `25` has no trailing \b and `13` has no leading \b: both survive
# as the literal `25t13`. Any service that logs a timestamp inside the message
# body (a structured JSON logger, say) therefore got a *different* fingerprint
# every hour and every day, which silently defeated finding dedup and made
# fingerprint-keyed ignore rules expire on their own. Seconds are optional and a
# bare wall-clock time is matched too, since loggers print both.
TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?(?:z|[+-]\d{2}:?\d{2})?"
    r"|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?",
    re.IGNORECASE,
)
NOISE_WORDS = re.compile(
    r"([a-f0-9]{8,}|[0-9a-f]{4,}-[0-9a-f-]{12,}|\b\d+\b|0x[a-f0-9]+)",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")
# A `level`/`severity` field a logger wrote about itself, in either of the two
# serializations we see: logfmt (`level=info`) and JSON (`"level": "INFO"`).
# Trusted over the keyword scan below, which cannot tell a line reporting an
# error from one merely containing the word — an info line that quotes an error
# string (Loki logging the text of a query, or a job reporting "(0 errors)") is
# not an anomaly.
_LEVEL_NAMES = r"trace|debug|info|warn|warning|error|fatal|panic|critical"
_LEVEL_KEYS = r"level|levelname|severity|lvl"
DECLARED_LEVEL = re.compile(
    rf"(?:^|\s)(?:{_LEVEL_KEYS})=\"?({_LEVEL_NAMES})\b"
    rf"|\"(?:{_LEVEL_KEYS})\"\s*:\s*\"({_LEVEL_NAMES})\"",
    re.IGNORECASE,
)


# Synonyms a logger may use for the four levels the rest of the system knows
# about. Anything unrecognised is passed through as-is rather than guessed at.
_LEVEL_SYNONYMS = {
    "trace": "info",
    "debug": "info",
    "warn": "warning",
    "err": "error",
    "critical": "error",
    "crit": "error",
    "panic": "fatal",
    "emergency": "fatal",
}


def _normalize_level(value: str) -> str:
    lowered = value.strip().lower()
    return _LEVEL_SYNONYMS.get(lowered, lowered)


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
            return _normalize_level(explicit)
        declared = DECLARED_LEVEL.search(self.line)
        if declared:
            # One group per serialization; exactly one of them matched.
            return _normalize_level(declared.group(1) or declared.group(2))
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
    # Non-signal lines the same service logged around the failure, and how many
    # lines it logged in the window overall. Whether a failure is deterministic or
    # intermittent is often the whole diagnosis — one 500 among hundreds of 200s
    # rules out a parameter bug on its face — and neither is visible in the error
    # lines alone. Both default to empty so payloads written before they existed,
    # and callers handing in a pre-filtered stream, still load.
    context: list[str] = field(default_factory=list)
    observed_events: int = 0

    def to_prompt_block(self) -> str:
        sample_text = "\n".join(f"- {line}" for line in self.samples[:8])
        labels = json.dumps(self.labels, sort_keys=True)
        block = (
            f"fingerprint: {self.fingerprint}\n"
            f"service: {self.service}\n"
            f"level: {self.level}\n"
            f"count: {self.count}\n"
            f"first_seen_utc: {self.first_seen_ns}\n"
            f"last_seen_utc: {self.last_seen_ns}\n"
            f"labels: {labels}\n"
        )
        if self.observed_events:
            block += (
                f"service_log_volume: {self.count} matching line(s) out of "
                f"{self.observed_events} that {self.service} logged in this window\n"
            )
        block += f"samples:\n{sample_text}"
        if self.context:
            context_text = "\n".join(f"- {line}" for line in self.context)
            block += (
                "\nsurrounding_lines (other lines the same service logged around "
                f"these, not themselves errors):\n{context_text}"
            )
        return block


# A cause the analyst did not actually determine. Remediation is gated on this:
# a repair prompt that carries "unknown" as the cause still produces a patch, and
# that patch lands at the crash site rather than at the defect. Matching is on the
# opening clause, so "Unknown. The logs do not identify a caller." counts too.
_UNKNOWN_CAUSE = re.compile(
    r"^(?:unknown|unclear|undetermined|indeterminate|not determined|"
    r"cannot be determined|no(?:t)? identified|insufficient evidence)$"
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

    @property
    def cause_is_unknown(self) -> bool:
        opening = re.split(r"[.;\n]", self.suspected_cause.strip(), maxsplit=1)[0]
        return not opening.strip() or bool(
            _UNKNOWN_CAUSE.match(opening.strip().strip("*_\"'` ").lower())
        )

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
    normalized = TIMESTAMP.sub("<ts>", line.strip().lower())
    normalized = NOISE_WORDS.sub("<var>", normalized)
    return WHITESPACE.sub(" ", normalized)


def fingerprint_for(event: LogEvent) -> str:
    content = {
        "service": event.service,
        "level": event.level,
        "line": normalize_line(event.line),
    }
    raw = json.dumps(content, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
