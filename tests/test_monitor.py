from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from guiltyspark.config import Settings
from guiltyspark.models import Finding, Incident, LogEvent
from guiltyspark.monitor import FleetMonitor, Monitor
from guiltyspark.remediation import RemediationResult
from guiltyspark.targets import Target


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        loki_url="http://loki.invalid:3100",
        loki_query='{job=~".+"}',
        loki_limit=100,
        interval_seconds=300,
        lookback_seconds=900,
        state_path=tmp_path / "state.sqlite3",
        findings_path=tmp_path / "findings.jsonl",
        min_events=2,
        max_incidents_per_run=8,
        model=None,
        runbook_path=None,
        notify_webhook_url=None,
        resend_api_key=None,
        notify_email_from=None,
        notify_email_to=None,
        codex_workdir=tmp_path,
        codex_home=tmp_path,
        codex_path="codex",
        codex_timeout_seconds=60,
        pr_mode="off",
    )


def _target(target_id: str, query: str) -> Target:
    return Target.from_dict(
        {
            "id": target_id,
            "loki_url": "http://loki.invalid:3100",
            "loki_query": query,
            "github_repo": f"owner/{target_id}",
        }
    )


def _monitor(tmp_path: Path, events: list[LogEvent]) -> Monitor:
    class FakeLoki:
        def query_range(self, **kwargs):
            return list(events)

    class FakeAnalyzer:
        async def analyze(self, *args, **kwargs):
            return []

    class FakeRepoDocs:
        def expected_logs(self, target):
            return None

    monitor = Monitor(_settings(tmp_path))
    monitor.loki = FakeLoki()  # type: ignore[assignment]
    monitor.analyzer = FakeAnalyzer()  # type: ignore[assignment]
    monitor.repo_docs = FakeRepoDocs()  # type: ignore[assignment]
    return monitor


def _events(count: int, first_ts_ns: int) -> list[LogEvent]:
    return [
        LogEvent(ts_ns=first_ts_ns + index, labels={"job": "web"}, line=f"boom {index}")
        for index in range(count)
    ]


def test_cursor_resumes_from_last_event_when_query_is_truncated(tmp_path):
    # A full page means Loki withheld the rest of the window; the cursor must not
    # jump past the events we never saw.
    events = _events(100, first_ts_ns=1_000)
    monitor = _monitor(tmp_path, events)

    summary = asyncio.run(monitor.run_once())

    assert summary.truncated is True
    assert summary.cursor_ns == events[-1].ts_ns + 1
    assert summary.cursor_ns < summary.end_ns
    assert monitor.state.get_cursor_ns("default") == events[-1].ts_ns + 1


def test_cursor_advances_to_window_end_when_query_is_complete(tmp_path):
    monitor = _monitor(tmp_path, _events(3, first_ts_ns=1_000))

    summary = asyncio.run(monitor.run_once())

    assert summary.truncated is False
    assert summary.cursor_ns == summary.end_ns
    assert monitor.state.get_cursor_ns("default") == summary.end_ns


def test_empty_result_still_advances_cursor(tmp_path):
    # No events is not truncation: a quiet window must not pin the cursor forever.
    monitor = _monitor(tmp_path, [])

    summary = asyncio.run(monitor.run_once())

    assert summary.truncated is False
    assert monitor.state.get_cursor_ns("default") == summary.end_ns


def test_fleet_monitor_hot_reloads_target_set(tmp_path):
    current = [_target("web", "{a=1}")]
    fleet = FleetMonitor(_settings(tmp_path), load_targets=lambda: list(current))

    monitors = fleet._sync()
    assert [m.target_id for m in monitors] == ["web"]
    web_monitor = monitors[0]

    # Add a target; the existing monitor instance is reused, the new one appears.
    current.append(_target("api", "{b=2}"))
    monitors = fleet._sync()
    assert sorted(m.target_id for m in monitors) == ["api", "web"]
    assert any(m is web_monitor for m in monitors)

    # Remove a target; it drops out of the live set.
    current[:] = [_target("api", "{b=2}")]
    monitors = fleet._sync()
    assert [m.target_id for m in monitors] == ["api"]


def test_fleet_monitor_rebuilds_monitor_on_config_change(tmp_path):
    current = [_target("web", "{a=1}")]
    fleet = FleetMonitor(_settings(tmp_path), load_targets=lambda: list(current))

    first = fleet._sync()[0]
    # Same id, different query -> monitor is rebuilt, not reused.
    current[:] = [_target("web", "{a=999}")]
    second = fleet._sync()[0]
    assert second is not first
    assert second.target.loki_query == "{a=999}"


def test_observe_holds_remediation_until_operator_releases_it(tmp_path):
    settings = replace(_settings(tmp_path), dedup_issues=False)
    observe = _target("web", "{a=1}")
    monitor = Monitor(settings, observe)
    incident = Incident(
        fingerprint="fp-observed",
        service="web",
        level="error",
        first_seen_ns=1,
        last_seen_ns=2,
        count=2,
        labels={"service": "web"},
        samples=["request handler crashed"],
    )
    finding = Finding(
        fingerprint=incident.fingerprint,
        title="Keep the request handler alive",
        severity="high",
        summary="The request handler crashed.",
        evidence=["request handler crashed"],
        suspected_cause="An unchecked error path.",
        recommended_fix="Handle the error and add a regression test.",
        pr_recommended=True,
        raw={},
    )

    attempted = asyncio.run(monitor._remediate([finding], [incident]))

    assert attempted == 0
    assert monitor.state.held_remediation_jobs("web") == 1
    assert monitor.state.pending_remediation_jobs("web") == []

    assert monitor.state.release_held_remediation_jobs("web") == 1
    monitor.target = Target.from_dict(
        {
            "id": "web",
            "loki_url": "http://loki.invalid:3100",
            "loki_query": "{a=1}",
            "github_repo": "owner/web",
            "mode": "pr",
            "test_commands": ["pytest"],
            "allowed_paths": ["src", "tests"],
        }
    )

    class FakeRemediator:
        def repair(self, target, queued_incident, queued_finding):
            assert target.mode == "pr"
            assert queued_incident.fingerprint == incident.fingerprint
            return RemediationResult(
                "pr-opened",
                "verification complete",
                branch="guiltyspark/fp-observed",
                pr_url="https://github.com/owner/web/pull/1",
            )

    monitor.remediator = FakeRemediator()  # type: ignore[assignment]

    class DisabledEmail:
        enabled = False

        def send_pr_opened(self, *args):
            return None

    monitor.email_notifier = DisabledEmail()  # type: ignore[assignment]

    assert asyncio.run(monitor._remediate([], [])) == 1
    assert monitor.state.pending_remediation_jobs("web") == []
