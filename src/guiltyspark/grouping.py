from __future__ import annotations

from bisect import bisect_left
from collections import Counter

from guiltyspark.models import Incident, LogEvent, fingerprint_for


IMPORTANT_LEVELS = {"fatal", "error", "warning"}
SIGNAL_TERMS = (
    "panic",
    "traceback",
    "exception",
    "timeout",
    "timed out",
    "refused",
    "unhealthy",
    "restart",
    "oom",
    "killed",
    "failed",
    "denied",
    "unauthorized",
    "forbidden",
    "rate limit",
    "retry",
)


# How far from a failure another line still counts as surrounding context, and how
# many such lines are worth the prompt tokens. Two seconds is wide enough to catch
# the concurrent requests that identify a contention bug, narrow enough to exclude
# unrelated chatter.
CONTEXT_WINDOW_NS = 2_000_000_000
MAX_CONTEXT_LINES = 6


def is_signal(event: LogEvent) -> bool:
    lowered = event.line.lower()
    return event.level in IMPORTANT_LEVELS or any(term in lowered for term in SIGNAL_TERMS)


def _context_lines(quiet: list[LogEvent], occurrences: list[int]) -> list[str]:
    """The non-signal lines nearest in time to any occurrence of the incident."""
    anchors = sorted(occurrences)
    near: list[tuple[int, int, str]] = []
    for event in quiet:
        position = bisect_left(anchors, event.ts_ns)
        candidates = anchors[max(0, position - 1) : position + 1]
        distance = min(abs(event.ts_ns - ts) for ts in candidates)
        if distance <= CONTEXT_WINDOW_NS:
            near.append((distance, event.ts_ns, event.line))
    near.sort()
    chosen: dict[str, int] = {}
    for _, ts_ns, line in near:
        if line in chosen or len(chosen) >= MAX_CONTEXT_LINES:
            continue
        chosen[line] = ts_ns
    return sorted(chosen, key=lambda line: chosen[line])


def group_incidents(
    events: list[LogEvent], min_events: int, include_context: bool = True
) -> list[Incident]:
    """Collapse raw log lines into incidents.

    ``include_context`` off for a caller that has already filtered the stream down
    to errors: there are no surrounding lines left to find, and a volume ratio
    computed from that stream would describe the filter, not the service.
    """
    groups: dict[str, Incident] = {}
    total_by_fingerprint: Counter[str] = Counter()
    occurrences: dict[str, list[int]] = {}
    quiet_by_service: dict[str, list[LogEvent]] = {}
    events_by_service: Counter[str] = Counter()

    for event in events:
        events_by_service[event.service] += 1
        if not is_signal(event):
            if include_context:
                quiet_by_service.setdefault(event.service, []).append(event)
            continue
        fingerprint = fingerprint_for(event)
        total_by_fingerprint[fingerprint] += 1
        occurrences.setdefault(fingerprint, []).append(event.ts_ns)
        incident = groups.get(fingerprint)
        if incident is None:
            groups[fingerprint] = Incident(
                fingerprint=fingerprint,
                service=event.service,
                level=event.level,
                first_seen_ns=event.ts_ns,
                last_seen_ns=event.ts_ns,
                count=1,
                labels=event.labels,
                samples=[event.line],
            )
            continue

        incident.count += 1
        incident.last_seen_ns = max(incident.last_seen_ns, event.ts_ns)
        if len(incident.samples) < 12 and event.line not in incident.samples:
            incident.samples.append(event.line)

    incidents = [
        incident
        for incident in groups.values()
        if incident.count >= min_events or incident.level in {"fatal", "error"}
    ]
    if include_context:
        for incident in incidents:
            incident.observed_events = events_by_service[incident.service]
            incident.context = _context_lines(
                quiet_by_service.get(incident.service, []),
                occurrences[incident.fingerprint],
            )
    incidents.sort(key=lambda item: (item.level != "fatal", item.level != "error", -item.count))
    return incidents
