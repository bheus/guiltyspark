from __future__ import annotations

import asyncio
from pathlib import Path

from guiltyspark.config import Settings
from guiltyspark.models import LogEvent
from guiltyspark.monitor import FleetMonitor, Monitor
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
