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
from guiltyspark.config import Settings
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


def selector_matches(matchers: list[LabelMatcher], labels: dict[str, str]) -> bool:
    return bool(matchers) and all(matcher.matches(labels) for matcher in matchers)


def tail_findings(path: Path, limit: int) -> list[dict]:
    """Return the newest `limit` findings from the JSONL findings log."""
    if not path.exists():
        return []
    kept: deque[dict] = deque(maxlen=limit)
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
    return list(reversed(kept))


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
        return {
            "generated_at": _now_iso(),
            "modes": sorted(VALID_MODES),
            "targets": [target_to_payload(target) for target in self._targets()],
        }

    def save_target(self, payload: dict) -> dict:
        """Validate and upsert a single target. Raises ValueError on bad input."""
        if not isinstance(payload, dict):
            raise ValueError("target payload must be a JSON object")
        target = Target.from_dict(payload)
        self.state.upsert_target(target.id, target_to_payload(target))
        return {"target": target_to_payload(target)}

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

    def add_ignore_rule(self, service: str, pattern: str, note: str = "") -> dict:
        cleaned = validate_pattern(pattern)
        rule_id = self.state.add_ignore_rule(service.strip(), cleaned, note.strip())
        return {"created": True, "id": rule_id, "pattern": cleaned}

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

    def findings(self, limit: int = 50) -> dict:
        return {
            "generated_at": _now_iso(),
            "findings": tail_findings(self.settings.findings_path, limit),
        }

    def remediations(self, limit: int = 50) -> dict:
        return {
            "generated_at": _now_iso(),
            "remediations": self.state.recent_remediations(limit),
        }

    def anomalies(self, minutes: int = 60) -> dict:
        end_ns = time.time_ns()
        start_ns = end_ns - minutes * 60 * 1_000_000_000
        client = LokiClient(
            base_url=self.settings.loki_url,
            bearer_token=self.settings.loki_bearer_token,
            basic_auth=self.settings.loki_basic_auth,
        )
        events = client.query_range(
            query=self.settings.loki_query,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=self.settings.loki_limit,
        )
        errors = [event for event in events if event.level in ANOMALY_LEVELS]
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
            "total_events": len(events),
            "truncated": len(events) >= self.settings.loki_limit,
            "error_events": len(errors),
            "ignored_count": ignored_count,
            "incidents": payload,
            "timeline": _timeline(errors, start_ns, end_ns),
        }
        groups = self._grouped_unassigned(unassigned, entry_by_fp)
        if groups is not None:
            response["groups"] = groups
        return response

    def _grouped_unassigned(
        self, incidents: list[Incident], entry_by_fp: dict[str, dict]
    ) -> list[dict] | None:
        """Codex-clustered view of the unassigned incidents, or None when
        grouping is disabled or unavailable (the UI then falls back to a flat
        list). A Codex failure degrades silently rather than breaking the view."""
        if not self.settings.dashboard_grouping or not incidents:
            return None
        try:
            clusters = self._cluster_cached(incidents)
        except Exception:
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

    def _cluster_cached(self, incidents: list[Incident]) -> list[AnomalyGroup]:
        signature = tuple(sorted(incident.fingerprint for incident in incidents))
        with self._cluster_lock:
            if signature == self._cluster_sig:
                return self._cluster_groups
            groups = cluster_incidents(self.settings, incidents)
            self._cluster_sig = signature
            self._cluster_groups = groups
            return groups

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
            elif parsed.path == "/api/findings":
                self._send_json(self.service.findings(_bounded(query, "limit", 50, 500)))
            elif parsed.path == "/api/remediations":
                self._send_json(
                    self.service.remediations(_bounded(query, "limit", 50, 500))
                )
            elif parsed.path == "/api/anomalies":
                self._send_json(
                    self.service.anomalies(_bounded(query, "minutes", 60, 1440))
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
        if "/" in name or name.startswith("."):
            self.send_error(404)
            return
        resource = resources.files("guiltyspark").joinpath("web", name)
        if not resource.is_file():
            self.send_error(404)
            return
        body = resource.read_bytes()
        suffix = Path(name).suffix
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
