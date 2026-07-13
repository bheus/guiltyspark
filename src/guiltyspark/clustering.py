"""Codex-assisted semantic clustering of live anomalies.

The deterministic grouping in `grouping.py` splits incidents by normalized log
line, so two logs describing the same underlying malfunction (e.g. an error
*processing* vs. *notifying* the same subsystem) land in separate incidents.
This module asks Codex to merge those into a smaller set of semantic groups so
the dashboard can present — and let an operator silence — one class at a time.

The result is a mapping only (title/summary + member fingerprints); the caller
re-derives live aggregates (counts, last-seen) from the current incidents, so a
cached clustering stays valid while its members' counts keep moving.
"""

from __future__ import annotations

from dataclasses import dataclass

from guiltyspark.codex import codex_exec, extract_json
from guiltyspark.config import Settings
from guiltyspark.models import Incident

CLUSTER_INSTRUCTIONS = """You are GuiltySpark, triaging live error logs for an operator.

You are given a list of distinct log incidents, each with a stable `fingerprint`.
Group them into clusters that share the same underlying malfunction — the same
subsystem failing for the same reason — even when their exact wording differs.
Keep unrelated problems in separate clusters. Do not merge merely because two
incidents share a service; merge only when a fix or a decision to silence would
sensibly apply to all members at once. A cluster may contain a single incident.

Every supplied fingerprint must appear in exactly one cluster. Do not invent
fingerprints that were not supplied.

Return only JSON with this shape:
{
  "groups": [
    {
      "title": "short operator-facing label for the class",
      "summary": "one sentence: what these incidents have in common",
      "fingerprints": ["fingerprint", "..."]
    }
  ]
}
"""


PATTERN_INSTRUCTIONS = """You are GuiltySpark, helping an operator silence a class of noisy logs.

You are given several example log lines that the operator has judged to be one
class of noise, all from the same service. Produce a single Python-compatible
regular expression (`re` syntax) that an operator would use to suppress this
class — now and for future variants of the SAME underlying malfunction.

Rules for the pattern:
- Anchor on the stable, meaningful text of the message (e.g. the `msg="..."`
  content or a distinctive phrase). Do NOT match on volatile tokens: timestamps,
  trace/span IDs, hex, UUIDs, line numbers, counts, durations.
- Be CONSERVATIVE. It is far worse to silence an unrelated future error than to
  miss a variant. Prefer a pattern that clearly identifies this malfunction over
  a broad one. Never emit `.*` or `.+` alone, or a pattern that would match any
  error line.
- Use `.*` only to bridge over volatile tokens between stable anchors.
- The pattern is applied with re.search against each raw log line.

Return only JSON with this shape:
{
  "pattern": "the regular expression",
  "explanation": "one sentence: what this matches and why it is safe"
}
"""


@dataclass(frozen=True)
class AnomalyGroup:
    """A semantic cluster of incident fingerprints. Aggregates are derived later."""

    title: str
    summary: str
    fingerprints: list[str]


def cluster_incidents(
    settings: Settings, incidents: list[Incident]
) -> list[AnomalyGroup]:
    """Cluster `incidents` via Codex. Fingerprints never assigned by the model
    are returned as their own single-member groups so nothing is dropped."""
    if not incidents:
        return []
    if len(incidents) == 1:
        only = incidents[0]
        return [_singleton(only)]

    prompt = _prompt(incidents)
    payload = extract_json(codex_exec(settings, prompt))
    return _groups_from_payload(payload, incidents)


def propose_pattern(settings: Settings, service: str, samples: list[str]) -> dict:
    """Ask Codex for a conservative regex covering a class of noisy log lines.

    Returns {"pattern": str, "explanation": str}. The caller is responsible for
    validating the pattern and letting the operator review it before it becomes
    a suppression rule — nothing here is auto-applied.
    """
    lines = "\n".join(f"- {line}" for line in samples[:20] if line)
    prompt = (
        f"{PATTERN_INSTRUCTIONS}\n\n"
        f"Service: {service or 'unknown'}\n\n"
        f"Example log lines:\n{lines}"
    )
    payload = extract_json(codex_exec(settings, prompt))
    return {
        "pattern": str(payload.get("pattern", "")).strip(),
        "explanation": str(payload.get("explanation", "")).strip(),
    }


def _prompt(incidents: list[Incident]) -> str:
    blocks = "\n\n---\n\n".join(
        incident.to_prompt_block() for incident in incidents
    )
    return f"{CLUSTER_INSTRUCTIONS}\n\nIncidents:\n\n{blocks}"


def _groups_from_payload(
    payload: dict, incidents: list[Incident]
) -> list[AnomalyGroup]:
    by_fp = {incident.fingerprint: incident for incident in incidents}
    valid = set(by_fp)
    assigned: set[str] = set()
    groups: list[AnomalyGroup] = []

    for raw in payload.get("groups", []):
        if not isinstance(raw, dict):
            continue
        members: list[str] = []
        for fp in raw.get("fingerprints", []):
            if isinstance(fp, str) and fp in valid and fp not in assigned:
                assigned.add(fp)
                members.append(fp)
        if not members:
            continue
        title = str(raw.get("title", "")).strip() or by_fp[members[0]].service
        summary = str(raw.get("summary", "")).strip()
        groups.append(
            AnomalyGroup(title=title, summary=summary, fingerprints=members)
        )

    # Fail safe: any incident Codex omitted becomes its own group, in input order.
    for incident in incidents:
        if incident.fingerprint not in assigned:
            groups.append(_singleton(incident))

    return groups


def _singleton(incident: Incident) -> AnomalyGroup:
    sample = incident.samples[0] if incident.samples else incident.service
    return AnomalyGroup(
        title=incident.service,
        summary=sample,
        fingerprints=[incident.fingerprint],
    )
