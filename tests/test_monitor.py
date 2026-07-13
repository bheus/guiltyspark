from __future__ import annotations

from pathlib import Path

from guiltyspark.config import Settings
from guiltyspark.monitor import FleetMonitor
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
