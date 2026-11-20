"""Fixed data the e2e dashboards serve: state rows, findings, and Loki events.

Every timestamp is relative to FROZEN_NOW, which the fixture server also pins the
process clock to, so ages, windows and sparklines render identically every run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from guiltyspark.state import StateStore
from guiltyspark.targets import Target, target_to_payload

FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
FROZEN_NOW_NS = int(FROZEN_NOW.timestamp()) * 1_000_000_000
FAKES_URL = "http://127.0.0.1:8097"

FINDINGS = 23
BUSY_COUNTS = {"findings": FINDINGS + 2, "remediations": 3, "prs_opened": 1}
EMPTY_COUNTS = {"findings": 0, "remediations": 0, "prs_opened": 0}

# Services the action tests mutate; nothing else asserts on them.
ACTION_SERVICES = ("cache-warmer", "cron-alpha", "cron-beta", "noisy-exporter")


def _ago(minutes: float) -> int:
    return FROZEN_NOW_NS - int(minutes * 60 * 1_000_000_000)


def _sql_time(minutes: float) -> str:
    return (FROZEN_NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def targets() -> list[Target]:
    return [
        Target(
            id="checkout",
            loki_url=FAKES_URL,
            loki_query='{container="checkout-api"}',
            github_repo="example/checkout",
            mode="draft-pr",
            test_commands=("npm test",),
            allowed_paths=("src/",),
        ),
        Target(
            id="ledger",
            loki_url=FAKES_URL,
            loki_query='{container="ledger-worker"}',
            github_repo="example/ledger",
        ),
    ]


# (container, level label, line template, occurrences, minutes ago of the newest)
_BUSY_STREAMS = [
    ("checkout-api", "error", "payment capture failed: card processor timeout after 30s", 14, 2),
    ("checkout-api", "fatal", "panic: nil pointer dereference in cart.Total", 3, 6),
    ("ledger-worker", "error", "ledger sync rejected: balance mismatch on account", 7, 11),
    ("image-proxy", "error", "upstream fetch failed: connection reset by peer", 9, 4),
    ("image-proxy", "error", "resize worker crashed: out of memory decoding webp", 2, 19),
    ("search-indexer", "error", "index flush failed: disk quota exceeded", 5, 8),
    ("mailer", "error", "smtp send failed: 421 too many connections", 4, 27),
    ("legacy-cron", "error", "nightly export failed: permission denied", 6, 33),
    ("cache-warmer", "error", "warm request failed: upstream returned 502", 3, 5),
    ("cron-alpha", "error", "job alpha failed: lock already held", 2, 9),
    ("cron-beta", "error", "job beta failed: lock already held by alpha", 2, 9),
    ("noisy-exporter", "error", "scrape failed: target down http://exporter:9100", 5, 12),
]

SILENCED_LINE = "nightly export failed: permission denied"


def loki_events(state: str) -> list[dict]:
    """Loki `streams` results: one stream per (container, level)."""
    if state == "empty":
        return []
    streams = []
    for container, level, line, count, newest in _BUSY_STREAMS:
        values = [
            [str(_ago(newest + index * 3)), f"level={level} msg=\"{line}\""]
            for index in range(count)
        ]
        streams.append({"stream": {"container": container, "level": level}, "values": values})
    return streams


def containers(state: str) -> list[str]:
    return [] if state == "empty" else sorted({s[0] for s in _BUSY_STREAMS})


def total_events(state: str) -> int:
    return 0 if state == "empty" else 4_812


def seed(state: str, store: StateStore, findings_path: Path) -> None:
    findings_path.write_text("")
    if state == "empty":
        return
    for target in targets():
        store.upsert_target(target.id, target_to_payload(target))
    _seed_findings(store, findings_path)
    _seed_remediations(store)
    _seed_silenced(store)


def _seed_findings(store: StateStore, findings_path: Path) -> None:
    severities = ["critical", "high", "medium", "low"]
    with findings_path.open("w", encoding="utf-8") as handle:
        for index in range(FINDINGS):
            finding = {
                "severity": severities[index % len(severities)],
                "title": f"Finding {index + 1:02d}: checkout payment capture timeouts",
                "created_at": (FROZEN_NOW - timedelta(hours=FINDINGS - index)).isoformat(),
                "summary": "Card processor calls exceed the 30s budget under load.",
                "suspected_cause": "Connection pool exhausted by retries without backoff.",
                "recommended_fix": "Cap retries and add jittered backoff in the capture client.",
                "evidence": ["payment capture failed: card processor timeout after 30s"],
            }
            handle.write(json.dumps(finding) + "\n")
            store.record_finding(f"hash-{index}", f"fp-{index}", finding["title"])
    store.record_target_finding("checkout", "fp-checkout-1", "Checkout capture timeouts")
    store.record_target_finding("ledger", "fp-ledger-1", "Ledger balance mismatch")


def _seed_remediations(store: StateStore) -> None:
    store.record_remediation(
        "checkout", "fp-checkout-1", "pr-opened", branch="guiltyspark/fix-capture-timeout",
        pr_url="https://github.com/example/checkout/pull/42",
    )
    store.record_remediation("checkout", "fp-checkout-2", "validated", branch="guiltyspark/fix-cart-total")
    store.record_remediation("ledger", "fp-ledger-1", "failed", details="tests failed")
    # created_at defaults to the database's real clock; pin it to the frozen one.
    with store._connect() as db:  # noqa: SLF001 - test seeding only
        for offset, row_id in enumerate(r[0] for r in db.execute("select id from remediations order by id")):
            db.execute("update remediations set created_at = ? where id = ?", (_sql_time(40 + offset * 90), row_id))


def _seed_silenced(store: StateStore) -> None:
    store.ignore_anomaly(
        "fp-silenced-healthcheck", note="Load balancer probe; harmless.",
        service="edge-proxy", level="error", sample="healthcheck failed: 503 from /ready", count=41,
    )
    store.add_ignore_rule(
        "legacy-cron", "nightly export failed", title="Legacy export", note="Retired job; owner notified.",
    )
