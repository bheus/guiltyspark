"""Deduplicate remediation on the *logical issue*, not the raw fingerprint.

A fingerprint is exact (`service + level + normalized line`), so one underlying
malfunction that logs slightly different wording or values each time it fires
produces a new fingerprint — and, under naive per-fingerprint dedup, a new pull
request every time. This module maps each candidate incident to a persistent,
Codex-assigned *issue* and gates PR creation on that issue:

* fingerprints Codex judges to be the same malfunction share one issue;
* an issue with a pull request still **open** on GitHub is never re-filed;
* an issue whose last PR merged/closed within a cooldown is not re-filed either,
  so a fix that has not yet reached the environment does not spawn duplicates;
* only one representative is remediated per issue per run.

Assignment persists in the state store, so a fingerprint seen in a later run
short-circuits without another Codex call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from guiltyspark.clustering import cluster_incidents
from guiltyspark.config import Settings
from guiltyspark.models import Finding, Incident

_SEVERITY_RANK = {
    "critical": 4,
    "fatal": 4,
    "high": 3,
    "error": 3,
    "medium": 2,
    "warning": 2,
    "low": 1,
    "info": 1,
}


@dataclass
class SuppressedIssue:
    issue_key: str
    fingerprints: list[str]
    reason: str  # "open-pr" | "cooldown"
    pr_url: str | None = None


@dataclass
class RemediationPlan:
    to_remediate: list[tuple[Finding, Incident]] = field(default_factory=list)
    suppressed: list[SuppressedIssue] = field(default_factory=list)


def plan_remediations(
    settings: Settings,
    state,
    target_id: str,
    candidates: list[tuple[Finding, Incident]],
    pr_status=None,
    now: float | None = None,
) -> RemediationPlan:
    """Resolve issues for ``candidates`` and decide which single representative
    per issue (if any) should be remediated."""
    plan = RemediationPlan()
    if not candidates:
        return plan

    mapping = _assign_issues(settings, state, target_id, candidates)

    grouped: dict[str, list[tuple[Finding, Incident]]] = {}
    for finding, incident in candidates:
        grouped.setdefault(mapping[incident.fingerprint], []).append((finding, incident))

    for issue_key, members in grouped.items():
        fingerprints = [inc.fingerprint for _, inc in members]
        block = _blocking_pr(settings, state, target_id, issue_key, pr_status, now)
        if block is not None:
            plan.suppressed.append(
                SuppressedIssue(
                    issue_key=issue_key,
                    fingerprints=fingerprints,
                    reason=block[0],
                    pr_url=block[1],
                )
            )
            continue
        plan.to_remediate.append(_representative(members))

    return plan


def _assign_issues(
    settings: Settings,
    state,
    target_id: str,
    candidates: list[tuple[Finding, Incident]],
) -> dict[str, str]:
    """Return ``{fingerprint: issue_key}`` for every candidate, persisting any
    newly discovered issues and memberships."""
    mapping: dict[str, str] = {}
    unknown: list[Incident] = []
    for _, incident in candidates:
        existing = state.issue_for_fingerprint(target_id, incident.fingerprint)
        if existing is not None:
            mapping[incident.fingerprint] = existing
        else:
            unknown.append(incident)

    if not unknown:
        return mapping

    if settings.dedup_issues:
        anchors = state.active_issues(target_id, settings.issue_active_window_seconds)
        try:
            assignments, new_issues = _cluster_assign(settings, unknown, anchors)
        except Exception as exc:  # Codex failure must not stall remediation
            print(f"issue_cluster_error target={target_id} error={exc}", flush=True)
            assignments, new_issues = _identity_assign(unknown)
    else:
        assignments, new_issues = _identity_assign(unknown)

    for issue_key, title, anchor in new_issues:
        sample = anchor.samples[0] if anchor.samples else anchor.service
        state.create_issue(
            target_id, issue_key, title, anchor.service, anchor.fingerprint, sample
        )
    for fingerprint, issue_key in assignments.items():
        state.record_issue_member(target_id, fingerprint, issue_key)
        mapping[fingerprint] = issue_key
    return mapping


def _cluster_assign(
    settings: Settings, unknown: list[Incident], anchors: list[dict]
) -> tuple[dict[str, str], list[tuple[str, str, Incident]]]:
    """Cluster unknown incidents against known-issue anchors via Codex.

    Returns ``({fingerprint: issue_key}, [(issue_key, title, anchor_incident)])``
    where the second list is the issues that must be created.
    """
    anchor_key_by_fp = {a["anchor_fingerprint"]: a["issue_key"] for a in anchors}
    pseudo = [
        Incident(
            fingerprint=a["anchor_fingerprint"],
            service=a["service"],
            level="",
            first_seen_ns=0,
            last_seen_ns=0,
            count=0,
            labels={},
            samples=[a["anchor_sample"]] if a["anchor_sample"] else [],
        )
        for a in anchors
        if a["anchor_fingerprint"]
    ]
    unknown_by_fp = {inc.fingerprint: inc for inc in unknown}
    groups = cluster_incidents(settings, pseudo + unknown)

    assignments: dict[str, str] = {}
    new_issues: list[tuple[str, str, Incident]] = []
    for group in groups:
        group_unknowns = [fp for fp in group.fingerprints if fp in unknown_by_fp]
        if not group_unknowns:
            continue
        existing = [
            anchor_key_by_fp[fp] for fp in group.fingerprints if fp in anchor_key_by_fp
        ]
        if existing:
            issue_key = existing[0]
        else:
            anchor = max(
                (unknown_by_fp[fp] for fp in group_unknowns), key=lambda i: i.count
            )
            issue_key = anchor.fingerprint
            new_issues.append((issue_key, group.title, anchor))
        for fp in group_unknowns:
            assignments[fp] = issue_key
    return assignments, new_issues


def _identity_assign(
    unknown: list[Incident],
) -> tuple[dict[str, str], list[tuple[str, str, Incident]]]:
    """Fallback: each incident is its own issue (still gated by the PR checks)."""
    assignments = {inc.fingerprint: inc.fingerprint for inc in unknown}
    new_issues = [(inc.fingerprint, inc.service, inc) for inc in unknown]
    return assignments, new_issues


def _blocking_pr(
    settings: Settings,
    state,
    target_id: str,
    issue_key: str,
    pr_status,
    now: float | None,
) -> tuple[str, str | None] | None:
    """Return ``(reason, pr_url)`` if a prior PR blocks re-filing this issue."""
    last = state.issue_last_pr(target_id, issue_key)
    if last is None:
        return None
    pr_url = last.get("pr_url")

    live = pr_status.status(pr_url) if pr_status is not None else None
    if live is not None and live.get("state") == "open":
        return ("open-pr", pr_url)

    age = _age_seconds(last.get("created_at"), now)
    if age is not None and age < settings.issue_cooldown_seconds:
        return ("cooldown", pr_url)
    return None


def _representative(
    members: list[tuple[Finding, Incident]],
) -> tuple[Finding, Incident]:
    """The most significant candidate: highest event count, then severity."""
    return max(
        members,
        key=lambda pair: (
            pair[1].count,
            _SEVERITY_RANK.get((pair[0].severity or "").lower(), 0),
        ),
    )


def _age_seconds(created_at: str | None, now: float | None) -> float | None:
    if not created_at:
        return None
    import time

    reference = time.time() if now is None else now
    text = created_at.strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return reference - parsed.timestamp()
