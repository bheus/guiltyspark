"""HTTP dashboard for guiltyspark.

Serves a JSON API over the monitor's state (findings, remediations) plus a live
Loki view that classifies recent error traffic into configured target buckets or
"unassigned". The static frontend consumes only the JSON API, so it can later be
replaced by a richer client (e.g. Vue) without backend changes.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from guiltyspark.clustering import AnomalyGroup, cluster_incidents, propose_pattern
from guiltyspark.codex import transport_readiness
from guiltyspark.config import Settings
from guiltyspark.github_pr import PrStatusClient
from guiltyspark.grouping import group_incidents
from guiltyspark.loki import LokiClient
from guiltyspark.models import Incident, LogEvent
from guiltyspark.state import StateStore
from guiltyspark.targets import (
    VALID_MODES,
    Target,
    load_targets_from_store,
    target_to_payload,
)

ANOMALY_LEVELS = {"error", "fatal"}
# Server-side prefilters mirroring LogEvent.level's two severity rules. They
# over-match on purpose (LogEvent.level still has the final say) — missing a
# real anomaly is far worse than fetching a line we then discard.
ANOMALY_LINE_PATTERN = "(?i)(panic|fatal|error|exception|traceback)"
# Lines that declare a non-anomaly level in logfmt: LogEvent.level believes the
# declaration over any keyword in the text, so there is no point fetching them.
_DECLARED_NON_ANOMALY_PATTERN = "(?i)(^|\\s)(level|severity|lvl)=\"?(trace|debug|info|warn|warning)\\b"
_ANOMALY_LEVEL_PATTERN = "(?i)^(error|fatal)$"
_SEVERITY_LABELS = ("level", "severity")
TIMELINE_BINS = 60
# Highest (most severe) first, for picking a group's headline level.
_SEVERITY_ORDER = ("fatal", "error", "warning", "info")
# Bounds for operator-reviewed silence patterns. Suppression hides signal, so we
# reject empty/degenerate regexes even though a human approved them.
_MAX_PATTERN_LEN = 1000
_MAX_MATCH_LEN = 2000
_TOO_BROAD = {"", ".", ".*", ".+", ".*?", ".+?", "^.*$", "^.+$", "(.*)", "(.+)"}


def validate_pattern(pattern: str) -> str:
    """Return a cleaned, compilable, non-degenerate regex or raise ValueError."""
    cleaned = (pattern or "").strip()
    if not cleaned:
        raise ValueError("pattern is required")
    if len(cleaned) > _MAX_PATTERN_LEN:
        raise ValueError(f"pattern exceeds {_MAX_PATTERN_LEN} characters")
    if cleaned in _TOO_BROAD:
        raise ValueError("pattern is too broad; it would silence unrelated errors")
    try:
        re.compile(cleaned)
    except re.error as exc:
        raise ValueError(f"pattern is not a valid regular expression: {exc}") from exc
    return cleaned


def _suppressed_by_rule(
    rules: list[tuple[str, re.Pattern[str]]], service: str, samples: list[str]
) -> bool:
    """True if any (service-scoped) rule regex matches one of the sample lines."""
    for rule_service, regex in rules:
        if rule_service and rule_service != service:
            continue
        for line in samples:
            if regex.search(line[:_MAX_MATCH_LEN]):
                return True
    return False

_MATCHER = re.compile(
    r'([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*("(?:[^"\\]|\\.)*"|`[^`]*`)'
)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}


@dataclass(frozen=True)
class LabelMatcher:
    name: str
    op: str
    value: str

    def matches(self, labels: dict[str, str]) -> bool:
        actual = labels.get(self.name, "")
        if self.op == "=":
            return actual == self.value
        if self.op == "!=":
            return actual != self.value
        pattern = re.compile(self.value)
        matched = pattern.fullmatch(actual) is not None
        return matched if self.op == "=~" else not matched


def parse_stream_selector(query: str) -> list[LabelMatcher]:
    """Parse the first `{...}` stream selector of a LogQL query into matchers.

    Only the label-matcher portion is understood; pipeline stages are ignored.
    Returns an empty list when no selector is found so callers fail open.
    """
    start = query.find("{")
    end = query.find("}", start)
    if start == -1 or end == -1:
        return []
    selector = query[start + 1 : end]
    matchers: list[LabelMatcher] = []
    for name, op, raw_value in _MATCHER.findall(selector):
        if raw_value.startswith("`"):
            value = raw_value[1:-1]
        else:
            value = json.loads(raw_value)
        if op in {"=~", "!~"}:
            try:
                re.compile(value)
            except re.error:
                continue
        matchers.append(LabelMatcher(name=name, op=op, value=value))
    return matchers


def with_label_filter(query: str, label: str, values: list[str]) -> str:
    """Inject `label=~"a|b"` into the first `{...}` stream selector of a LogQL
    query. Values are regex-escaped, so each one matches literally. Returns the
    query unchanged when there is nothing to filter or no selector is found
    (fail open, like parse_stream_selector)."""
    if not values:
        return query
    pattern = "|".join(re.escape(value) for value in values)
    return _with_matcher(query, f"{label}=~{json.dumps(pattern)}")


def _with_matcher(query: str, matcher: str) -> str:
    """Add a matcher to the first `{...}` stream selector, or return the query
    unchanged if there is no selector to extend (fail open)."""
    start = query.find("{")
    end = query.find("}", start)
    if start == -1 or end == -1:
        return query
    separator = ", " if query[start + 1 : end].strip() else ""
    return query[:end] + separator + matcher + query[end:]


def anomaly_queries(query: str) -> list[str]:
    """LogQL variants that together match a superset of what `LogEvent.level`
    calls error/fatal, so severity filtering happens in Loki instead of over a
    full download of the window.

    `LogEvent.level` reads a severity label first and falls back to scanning the
    line, and neither test implies the other: a line labelled `level=error` need
    not contain the word, and a line containing it may be labelled otherwise.
    So each rule gets its own query and the results are unioned; the caller
    still applies `LogEvent.level` to reject the extra lines this over-fetches.
    """
    # The exclusion keeps the keyword query honest about self-inflicted noise:
    # Loki logs each query it serves at info, echoing this very pattern back
    # into the logs, where the keywords would otherwise match themselves.
    queries = [
        f"{query} |~ {json.dumps(ANOMALY_LINE_PATTERN)} "
        f"!~ {json.dumps(_DECLARED_NON_ANOMALY_PATTERN)}"
    ]
    for label in _SEVERITY_LABELS:
        queries.append(_with_matcher(query, f"{label}=~{json.dumps(_ANOMALY_LEVEL_PATTERN)}"))
    return queries


def _merge_events(pages: list[list[LogEvent]]) -> list[LogEvent]:
    """Union overlapping result sets, keeping repeated identical lines.

    The severity queries overlap by design, so the same line usually arrives
    more than once. Deduplicating outright would erase genuine repeats of an
    identical line at the same instant, so each distinct line keeps the highest
    number of copies any single query saw.
    """
    best: dict[tuple, list[LogEvent]] = {}
    for page in pages:
        counts: dict[tuple, list[LogEvent]] = {}
        for event in page:
            key = (event.ts_ns, event.line, tuple(sorted(event.labels.items())))
            counts.setdefault(key, []).append(event)
        for key, events in counts.items():
            if len(events) > len(best.get(key, [])):
                best[key] = events
    merged = [event for events in best.values() for event in events]
    merged.sort(key=lambda event: event.ts_ns)
    return merged


def selector_matches(matchers: list[LabelMatcher], labels: dict[str, str]) -> bool:
    return bool(matchers) and all(matcher.matches(labels) for matcher in matchers)


def tail_findings(path: Path, limit: int, offset: int = 0) -> tuple[list[dict], int]:
    """Return one newest-first page of findings plus the total available.

    ``offset`` counts back from the newest entry, so ``(limit=30, offset=30)``
    yields the 31st–60th most recent findings. The JSONL log is read once; only
    the newest ``limit + offset`` payloads are retained to bound memory.
    """
    if not path.exists():
        return [], 0
    offset = max(0, offset)
    kept: deque[dict] = deque(maxlen=limit + offset)
    total = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload.pop("raw", None)
            kept.append(payload)
            total += 1
    page = list(reversed(kept))[offset : offset + limit]
    return page, total


class DashboardService:
    """Assembles the JSON payloads served by the dashboard."""

    def __init__(self, settings: Settings, targets: list[Target] | None = None) -> None:
        self.settings = settings
        self.state = StateStore(settings.state_path)
        # Codex clustering is expensive; cache it against the set of unassigned
        # fingerprints so the 60s auto-refresh only re-clusters when a genuinely
        # new anomaly class appears (count changes reuse the cached mapping).
        self._cluster_lock = threading.Lock()
        self._cluster_sig: tuple[str, ...] | None = None
        self._cluster_groups: list[AnomalyGroup] = []
        # The newest anomaly set awaiting clustering, and whether a worker is
        # already alive to take it. One worker at a time: a set that keeps
        # changing hands its latest state to the worker in flight rather than
        # starting a rival, so `codex exec` never runs concurrently with itself.
        self._cluster_wanted: tuple[str, ...] | None = None
        self._cluster_wanted_incidents: list[Incident] = []
        self._cluster_running = False
        # Resolves each opened PR's live disposition (merged/closed/open); its own
        # TTL cache keeps the auto-refresh from hammering the GitHub API.
        self._pr_status = PrStatusClient(settings)
        # Seed the store from any startup-provided targets, but only while it is
        # empty so live dashboard edits are never clobbered.
        if targets and self.state.count_targets() == 0:
            for target in targets:
                self.state.upsert_target(target.id, target_to_payload(target))

    def _targets(self) -> list[Target]:
        """Live target set, re-read from the store so edits reflect at once."""
        return load_targets_from_store(self.state)

    def overview(self) -> dict:
        counts = self.state.dashboard_counts()
        return {
            "generated_at": _now_iso(),
            "loki_url": self.settings.loki_url,
            "interval_seconds": self.settings.interval_seconds,
            "targets": [
                {
                    "id": target.id,
                    "github_repo": target.github_repo,
                    "mode": target.mode,
                    "loki_query": target.loki_query,
                }
                for target in self._targets()
            ],
            "counts": counts,
        }

    # --- target configuration (dashboard-editable) ----------------------

    def list_targets(self) -> dict:
        """Full target configs for the editor, in the store's canonical form."""
        targets = []
        for target in self._targets():
            payload = target_to_payload(target)
            payload["held_remediations"] = self.state.held_remediation_jobs(target.id)
            targets.append(payload)
        return {
            "generated_at": _now_iso(),
            "modes": sorted(VALID_MODES),
            "targets": targets,
        }

    def save_target(self, payload: dict) -> dict:
        """Validate and upsert a single target. Raises ValueError on bad input."""
        if not isinstance(payload, dict):
            raise ValueError("target payload must be a JSON object")
        release_observed = payload.get("release_observed", False) is True
        target = Target.from_dict(payload)
        previous = next(
            (
                item
                for item in self.state.list_target_payloads()
                if str(item.get("id", "")) == target.id
            ),
            None,
        )
        release_allowed = previous is not None and target.mode != "observe"
        if release_observed and not release_allowed:
            raise ValueError(
                "held anomalies may only be released for an active protocol"
            )
        self.state.upsert_target(target.id, target_to_payload(target))
        released = (
            self.state.release_held_remediation_jobs(target.id)
            if release_observed
            else 0
        )
        return {"target": target_to_payload(target), "released_remediations": released}

    def delete_target(self, target_id: str) -> dict:
        if not target_id:
            raise ValueError("target id is required")
        removed = self.state.delete_target(target_id)
        return {"deleted": removed, "id": target_id}

    # --- ignored anomalies (silenced noise) -----------------------------

    def ignored_anomalies(self) -> dict:
        return {
            "generated_at": _now_iso(),
            "ignored": self.state.list_ignored_anomalies(),
            "rules": self.state.list_ignore_rules(),
        }

    # --- pattern silence rules ------------------------------------------

    def _compiled_rules(self) -> list[tuple[str, re.Pattern[str]]]:
        """Live rules, compiled. Invalid regexes are skipped so one bad rule
        cannot break the anomaly view."""
        compiled: list[tuple[str, re.Pattern[str]]] = []
        for rule in self.state.list_ignore_rules():
            try:
                compiled.append(
                    (str(rule.get("service", "")), re.compile(str(rule["pattern"])))
                )
            except (re.error, KeyError):
                continue
        return compiled

    def suggest_pattern(self, service: str, samples: list) -> dict:
        """Codex-proposed regex for a class of anomalies. Proposal only — the
        operator reviews and commits it separately."""
        lines = [str(item) for item in samples if str(item).strip()]
        if not lines:
            raise ValueError("at least one sample line is required")
        proposal = propose_pattern(self.settings, service, lines)
        pattern = proposal.get("pattern", "")
        try:
            pattern = validate_pattern(pattern)
            warning = ""
        except ValueError as exc:
            # Surface the proposal anyway so the operator can fix it by hand.
            warning = str(exc)
        return {
            "service": service,
            "pattern": pattern,
            "explanation": proposal.get("explanation", ""),
            "warning": warning,
        }

    def add_ignore_rule(
        self, service: str, pattern: str, note: str = "", title: str = ""
    ) -> dict:
        cleaned = validate_pattern(pattern)
        cleaned_title = title.strip()
        if len(cleaned_title) > 200:
            raise ValueError("title exceeds 200 characters")
        rule_id = self.state.add_ignore_rule(
            service.strip(), cleaned, note.strip(), cleaned_title
        )
        return {
            "created": True,
            "id": rule_id,
            "pattern": cleaned,
            "title": cleaned_title,
        }

    def update_ignore_rule_metadata(self, rule_id: int, title: str, note: str) -> dict:
        if rule_id <= 0:
            raise ValueError("rule id is required")
        cleaned_title = title.strip()
        if len(cleaned_title) > 200:
            raise ValueError("title exceeds 200 characters")
        updated = self.state.set_ignore_rule_metadata(
            rule_id, cleaned_title, note.strip()
        )
        if not updated:
            raise ValueError(f"no silence rule with id {rule_id}")
        return {"updated": True, "id": rule_id, "title": cleaned_title}

    def delete_ignore_rule(self, rule_id: int) -> dict:
        removed = self.state.delete_ignore_rule(rule_id)
        return {"deleted": removed, "id": rule_id}

    def ignore_anomaly(
        self,
        fingerprint: str,
        note: str = "",
        service: str = "",
        level: str = "",
        sample: str = "",
        count: int = 0,
    ) -> dict:
        if not fingerprint:
            raise ValueError("fingerprint is required")
        self.state.ignore_anomaly(
            fingerprint,
            note=note,
            service=service,
            level=level,
            sample=sample,
            count=count,
        )
        return {"ignored": True, "fingerprint": fingerprint}

    def ignore_anomalies_batch(self, anomalies: list) -> dict:
        if not isinstance(anomalies, list):
            raise ValueError("anomalies must be a JSON array")
        items = [item for item in anomalies if isinstance(item, dict)]
        applied = self.state.ignore_anomalies(items)
        return {"ignored": True, "count": applied}

    def update_ignored_note(self, fingerprint: str, note: str) -> dict:
        if not fingerprint:
            raise ValueError("fingerprint is required")
        updated = self.state.set_ignored_note(fingerprint, note)
        if not updated:
            raise ValueError(f"no silenced anomaly with fingerprint {fingerprint!r}")
        return {"updated": True, "fingerprint": fingerprint}

    def unignore_anomaly(self, fingerprint: str) -> dict:
        if not fingerprint:
            raise ValueError("fingerprint is required")
        restored = self.state.unignore_anomaly(fingerprint)
        return {"restored": restored, "fingerprint": fingerprint}

    def findings(self, limit: int = 50, offset: int = 0) -> dict:
        page, total = tail_findings(self.settings.findings_path, limit, offset)
        return {
            "generated_at": _now_iso(),
            "findings": page,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def remediations(self, limit: int = 50, offset: int = 0) -> dict:
        records = self.state.recent_remediations(limit, offset)
        for record in records:
            status = self._pr_status.status(record.get("pr_url"))
            if status is not None:
                record["pr_state"] = status["state"]
                record["merged_at"] = status.get("merged_at")
                record["closed_at"] = status.get("closed_at")
        return {
            "generated_at": _now_iso(),
            "remediations": records,
            "total": self.state.count_remediations(),
            "limit": limit,
            "offset": offset,
        }

    def anomalies(self, minutes: int = 60, containers: list[str] | None = None) -> dict:
        containers = containers or []
        end_ns = time.time_ns()
        start_ns = end_ns - minutes * 60 * 1_000_000_000
        client = LokiClient(
            base_url=self.settings.loki_url,
            bearer_token=self.settings.loki_bearer_token,
            basic_auth=self.settings.loki_basic_auth,
        )
        base_query = with_label_filter(
            self.settings.loki_query,
            self.settings.dashboard_filter_label,
            containers,
        )
        # Severity is filtered in Loki and the denominator comes from a metric
        # query, so cost tracks the anomalies found rather than the window's
        # total volume — which runs to hundreds of thousands of lines at 24h.
        pages: list[list[LogEvent]] = []
        truncated = False
        for query in anomaly_queries(base_query):
            page, page_truncated = client.query_window(
                query=query,
                start_ns=start_ns,
                end_ns=end_ns,
                page_limit=self.settings.loki_limit,
                max_events=self.settings.dashboard_max_events,
            )
            pages.append(page)
            truncated = truncated or page_truncated
        candidates = _merge_events(pages)
        errors = [event for event in candidates if event.level in ANOMALY_LEVELS]
        total_events = client.count_over_window(base_query, start_ns, end_ns)
        incidents = group_incidents(errors, min_events=1)
        selectors = [
            (target.id, parse_stream_selector(target.loki_query))
            for target in self._targets()
        ]
        ignored = self.state.ignored_fingerprints()
        rules = self._compiled_rules()
        payload = []
        ignored_count = 0
        unassigned: list[Incident] = []
        entry_by_fp: dict[str, dict] = {}
        for incident in incidents:
            if incident.fingerprint in ignored or _suppressed_by_rule(
                rules, incident.service, incident.samples
            ):
                ignored_count += 1
                continue
            entry = asdict(incident)
            entry["bucket"] = _bucket_for(selectors, incident.labels)
            entry["samples"] = incident.samples[:5]
            payload.append(entry)
            entry_by_fp[incident.fingerprint] = entry
            if entry["bucket"] == "unassigned":
                unassigned.append(incident)
        response = {
            "generated_at": _now_iso(),
            "window_minutes": minutes,
            "containers": containers,
            "total_events": total_events,
            "truncated": truncated,
            "error_events": len(errors),
            "ignored_count": ignored_count,
            "incidents": payload,
            "timeline": _timeline(errors, start_ns, end_ns),
        }
        groups = self._grouped_unassigned(unassigned, entry_by_fp)
        if groups is not None:
            response["groups"] = groups
        elif self.settings.dashboard_grouping and unassigned:
            # Grouping is on and there are anomalies to cluster, but the result
            # is not cached yet — a worker is computing it off-thread. Signal the
            # UI so it can show a "cataloging" state over the flat fallback list.
            response["groups_pending"] = True
        return response

    def containers(self, minutes: int = 60) -> dict:
        """Values of the filter label (default `container`) seen in the window,
        from Loki's label-values API — so containers crowded out of the capped
        event sample still appear in the picker."""
        end_ns = time.time_ns()
        start_ns = end_ns - minutes * 60 * 1_000_000_000
        client = LokiClient(
            base_url=self.settings.loki_url,
            bearer_token=self.settings.loki_bearer_token,
            basic_auth=self.settings.loki_basic_auth,
        )
        values = client.label_values(
            self.settings.dashboard_filter_label, start_ns, end_ns
        )
        return {
            "generated_at": _now_iso(),
            "label": self.settings.dashboard_filter_label,
            "containers": values,
        }

    def _grouped_unassigned(
        self, incidents: list[Incident], entry_by_fp: dict[str, dict]
    ) -> list[dict] | None:
        """Codex-clustered view of the unassigned incidents, or None when
        grouping is disabled or unavailable (the UI then falls back to a flat
        list). A Codex failure degrades silently rather than breaking the view."""
        if not self.settings.dashboard_grouping or not incidents:
            return None
        clusters = self._cluster_cached(incidents)
        if clusters is None:
            # Clustering for this anomaly set is not ready yet (computing
            # off-thread); the UI shows the flat list until a later poll.
            return None
        display = []
        for index, cluster in enumerate(clusters):
            members = [
                entry_by_fp[fp] for fp in cluster.fingerprints if fp in entry_by_fp
            ]
            if not members:
                continue
            display.append(
                {
                    "id": f"g{index}",
                    "title": cluster.title,
                    "summary": cluster.summary,
                    "level": _headline_level(members),
                    "count": sum(m["count"] for m in members),
                    "last_seen_ns": max(m["last_seen_ns"] for m in members),
                    "services": sorted({m["service"] for m in members}),
                    "fingerprints": [m["fingerprint"] for m in members],
                    "members": members,
                }
            )
        return display

    def _cluster_cached(self, incidents: list[Incident]) -> list[AnomalyGroup] | None:
        """Return cached clusters for this anomaly set, or None while they are
        computed off-thread. Codex clustering is slow, so it never runs inline
        on the dashboard request path; a background worker fills the cache and a
        later poll picks it up."""
        signature = tuple(sorted(incident.fingerprint for incident in incidents))
        with self._cluster_lock:
            if signature == self._cluster_sig:
                return self._cluster_groups
            # Record the newest request and let any live worker collect it. On a
            # busy installation the anomaly set changes most polls, so spawning
            # per change would stack a Codex subprocess per poll on the host.
            self._cluster_wanted = signature
            self._cluster_wanted_incidents = list(incidents)
            if self._cluster_running:
                return None
            self._cluster_running = True
        threading.Thread(target=self._cluster_worker, daemon=True).start()
        return None

    def _cluster_worker(self) -> None:
        """Cluster off the request path until the newest request is satisfied.

        Loops rather than exiting after one pass: a set that changed while Codex
        was working is served on the next lap by this same worker, which is what
        keeps the count of live `codex exec` processes at one however fast polls
        arrive. Failures drop the attempt so a later poll can retry."""
        while True:
            with self._cluster_lock:
                signature = self._cluster_wanted
                incidents = self._cluster_wanted_incidents
                if signature is None or signature == self._cluster_sig:
                    self._clear_cluster_request()
                    return
            try:
                groups = cluster_incidents(self.settings, incidents)
            except Exception:
                # Drop the attempt rather than retry in-loop: a Codex outage
                # would otherwise spin here. The next poll re-requests.
                with self._cluster_lock:
                    self._clear_cluster_request()
                return
            with self._cluster_lock:
                self._cluster_sig = signature
                self._cluster_groups = groups
                if self._cluster_wanted == signature:
                    self._clear_cluster_request()
                    return
                # A newer set landed mid-flight; take it on the next lap.

    def _clear_cluster_request(self) -> None:
        """Retire the worker. Caller must hold `_cluster_lock`."""
        self._cluster_wanted = None
        self._cluster_wanted_incidents = []
        self._cluster_running = False

    def _bucket_for(self, labels: dict[str, str]) -> str:
        selectors = [
            (target.id, parse_stream_selector(target.loki_query))
            for target in self._targets()
        ]
        return _bucket_for(selectors, labels)


def _bucket_for(
    selectors: list[tuple[str, list[LabelMatcher]]], labels: dict[str, str]
) -> str:
    for target_id, matchers in selectors:
        if selector_matches(matchers, labels):
            return target_id
    return "unassigned"


def _headline_level(members: list[dict]) -> str:
    levels = {m["level"] for m in members}
    for level in _SEVERITY_ORDER:
        if level in levels:
            return level
    return next(iter(levels), "info")


def _timeline(events: list[LogEvent], start_ns: int, end_ns: int) -> list[dict]:
    span = max(end_ns - start_ns, 1)
    counts = [0] * TIMELINE_BINS
    for event in events:
        index = min((event.ts_ns - start_ns) * TIMELINE_BINS // span, TIMELINE_BINS - 1)
        if index >= 0:
            counts[int(index)] += 1
    step = span // TIMELINE_BINS
    return [
        {
            "t": _iso_from_ns(start_ns + i * step),
            "count": count,
        }
        for i, count in enumerate(counts)
    ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_from_ns(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).isoformat()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "guiltyspark-dashboard"

    @property
    def service(self) -> DashboardService:
        return self.server.service  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/overview":
                self._send_json(self.service.overview())
            elif parsed.path == "/api/readiness":
                readiness = transport_readiness()
                self._send_json(readiness, status=200 if readiness["ready"] else 503)
            elif parsed.path == "/api/findings":
                self._send_json(
                    self.service.findings(
                        _bounded(query, "limit", 50, 500),
                        _offset(query),
                    )
                )
            elif parsed.path == "/api/remediations":
                self._send_json(
                    self.service.remediations(
                        _bounded(query, "limit", 50, 500),
                        _offset(query),
                    )
                )
            elif parsed.path == "/api/anomalies":
                self._send_json(
                    self.service.anomalies(
                        _bounded(query, "minutes", 60, 1440),
                        _containers_param(query),
                    )
                )
            elif parsed.path == "/api/containers":
                self._send_json(
                    self.service.containers(_bounded(query, "minutes", 60, 1440))
                )
            elif parsed.path == "/api/anomalies/ignored":
                self._send_json(self.service.ignored_anomalies())
            elif parsed.path == "/api/targets":
                self._send_json(self.service.list_targets())
            else:
                self._send_static(parsed.path)
        except Exception as exc:  # surface backend failures to the UI as JSON
            self._send_json({"error": str(exc)}, status=502)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        try:
            body = self._read_json_body()
            if parsed.path == "/api/targets":
                self._send_json(self.service.save_target(body))
            elif parsed.path == "/api/anomalies/ignore":
                self._send_json(
                    self.service.ignore_anomaly(
                        str(body.get("fingerprint", "")).strip(),
                        note=str(body.get("note", "")).strip(),
                        service=str(body.get("service", "")).strip(),
                        level=str(body.get("level", "")).strip(),
                        sample=str(body.get("sample", "")).strip(),
                        count=_as_int(body.get("count")),
                    )
                )
            elif parsed.path == "/api/anomalies/ignore-batch":
                self._send_json(
                    self.service.ignore_anomalies_batch(body.get("anomalies", []))
                )
            elif parsed.path == "/api/anomalies/suggest-pattern":
                self._send_json(
                    self.service.suggest_pattern(
                        str(body.get("service", "")).strip(),
                        body.get("samples", []),
                    )
                )
            elif parsed.path == "/api/anomalies/rules":
                self._send_json(
                    self.service.add_ignore_rule(
                        str(body.get("service", "")),
                        str(body.get("pattern", "")),
                        str(body.get("note", "")),
                        str(body.get("title", "")),
                    )
                )
            elif parsed.path == "/api/anomalies/rules/metadata":
                self._send_json(
                    self.service.update_ignore_rule_metadata(
                        _as_int(body.get("id")),
                        str(body.get("title", "")),
                        str(body.get("note", "")),
                    )
                )
            elif parsed.path == "/api/anomalies/note":
                self._send_json(
                    self.service.update_ignored_note(
                        str(body.get("fingerprint", "")).strip(),
                        str(body.get("note", "")).strip(),
                    )
                )
            else:
                self.send_error(404)
        except ValueError as exc:  # validation / malformed request
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=502)

    def do_DELETE(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/targets":
                self._send_json(
                    self.service.delete_target(query.get("id", [""])[0].strip())
                )
            elif parsed.path == "/api/anomalies/ignore":
                self._send_json(
                    self.service.unignore_anomaly(
                        query.get("fingerprint", [""])[0].strip()
                    )
                )
            elif parsed.path == "/api/anomalies/rules":
                self._send_json(
                    self.service.delete_ignore_rule(
                        _as_int(query.get("id", [""])[0].strip())
                    )
                )
            else:
                self.send_error(404)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=502)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"request body is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> None:
        name = path.lstrip("/") or "index.html"
        parts = name.split("/")
        # Reject empty, hidden, or traversal segments outright.
        unsafe = any(
            (not part or part in (".", "..") or part.startswith("."))
            for part in parts
        )
        # The Vite bundle is flat top-level files (index.html) plus a single
        # assets/ subdirectory of hashed js/css; nothing deeper is served.
        is_asset = len(parts) == 2 and parts[0] == "assets"
        is_top = len(parts) == 1
        if unsafe or not (is_asset or is_top):
            self.send_error(404)
            return
        resource = resources.files("guiltyspark").joinpath("web", *parts)
        if not resource.is_file():
            self.send_error(404)
            return
        body = resource.read_bytes()
        suffix = Path(parts[-1]).suffix
        self.send_response(200)
        self.send_header(
            "Content-Type", _CONTENT_TYPES.get(suffix, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _bounded(query: dict[str, list[str]], key: str, default: int, maximum: int) -> int:
    raw = query.get(key, [str(default)])[0]
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, min(value, maximum))


_MAX_CONTAINER_FILTERS = 50
_MAX_CONTAINER_VALUE_LEN = 200


def _containers_param(query: dict[str, list[str]]) -> list[str]:
    """Container filter values from repeated and/or comma-separated `container`
    params, deduplicated in order; oversized values and excess entries drop."""
    values: list[str] = []
    for raw in query.get("container", []):
        for part in raw.split(","):
            part = part.strip()
            if (
                part
                and len(part) <= _MAX_CONTAINER_VALUE_LEN
                and part not in values
            ):
                values.append(part)
                if len(values) >= _MAX_CONTAINER_FILTERS:
                    return values
    return values


def _offset(query: dict[str, list[str]]) -> int:
    """Non-negative pagination offset; malformed or negative values clamp to 0."""
    raw = query.get("offset", ["0"])[0]
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def make_server(
    settings: Settings, targets: list[Target], host: str, port: int
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.service = DashboardService(settings, targets)  # type: ignore[attr-defined]
    return server


def serve(settings: Settings, targets: list[Target], host: str, port: int) -> None:
    server = make_server(settings, targets, host, port)
    print(f"dashboard listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
