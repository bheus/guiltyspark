from __future__ import annotations

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


def is_signal(event: LogEvent) -> bool:
    lowered = event.line.lower()
    return event.level in IMPORTANT_LEVELS or any(term in lowered for term in SIGNAL_TERMS)


def group_incidents(events: list[LogEvent], min_events: int) -> list[Incident]:
    groups: dict[str, Incident] = {}
    total_by_fingerprint: Counter[str] = Counter()

    for event in events:
        if not is_signal(event):
            continue
        fingerprint = fingerprint_for(event)
        total_by_fingerprint[fingerprint] += 1
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
    incidents.sort(key=lambda item: (item.level != "fatal", item.level != "error", -item.count))
    return incidents
