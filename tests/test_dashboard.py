from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from guiltyspark.config import Settings
from guiltyspark.dashboard import (
    DashboardService,
    make_server,
    parse_stream_selector,
    selector_matches,
    tail_findings,
)
from guiltyspark.state import StateStore
from guiltyspark.targets import Target


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
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
    defaults.update(overrides)
    return Settings(**defaults)


def _target(**overrides) -> Target:
    payload = dict(
        id="abraham",
        loki_url="http://loki.invalid:3100",
        loki_query='{container=~"abraham-(trading|dashboard)(-preprod)?"}',
        github_repo="owner/abraham",
    )
    payload.update(overrides)
    return Target.from_dict(payload)


class TestParseStreamSelector:
    def test_parses_regex_matcher(self):
        matchers = parse_stream_selector('{container=~"abraham-(trading|dashboard)"}')
        assert len(matchers) == 1
        assert matchers[0].name == "container"
        assert matchers[0].op == "=~"

    def test_parses_multiple_matchers(self):
        matchers = parse_stream_selector('{job="docker", container!="noise"} |= "err"')
        assert [(m.name, m.op, m.value) for m in matchers] == [
            ("job", "=", "docker"),
            ("container", "!=", "noise"),
        ]

    def test_backtick_values(self):
        matchers = parse_stream_selector('{container=~`abc\\d+`}')
        assert matchers[0].value == "abc\\d+"

    def test_no_selector_returns_empty(self):
        assert parse_stream_selector("count_over_time") == []


class TestSelectorMatches:
    def test_regex_is_anchored(self):
        matchers = parse_stream_selector('{container=~"abraham-(trading|dashboard)(-preprod)?"}')
        assert selector_matches(matchers, {"container": "abraham-trading"})
        assert selector_matches(matchers, {"container": "abraham-dashboard-preprod"})
        assert not selector_matches(matchers, {"container": "abraham-trading-extra"})
        assert not selector_matches(matchers, {"container": "xx-abraham-trading"})

    def test_missing_label_fails_equality(self):
        matchers = parse_stream_selector('{job="docker"}')
        assert not selector_matches(matchers, {"container": "abraham-trading"})

    def test_empty_matchers_never_match(self):
        assert not selector_matches([], {"container": "anything"})


class TestTailFindings:
    def test_returns_newest_first_without_raw(self, tmp_path):
        path = tmp_path / "findings.jsonl"
        lines = [
            json.dumps({"title": f"finding {i}", "raw": {"noise": True}})
            for i in range(5)
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        found = tail_findings(path, limit=3)
        assert [item["title"] for item in found] == ["finding 4", "finding 3", "finding 2"]
        assert all("raw" not in item for item in found)

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "findings.jsonl"
        path.write_text('not json\n{"title": "ok"}\n', encoding="utf-8")
        assert [item["title"] for item in tail_findings(path, 10)] == ["ok"]

    def test_missing_file(self, tmp_path):
        assert tail_findings(tmp_path / "absent.jsonl", 10) == []


class TestDashboardService:
    def test_bucket_classification(self, tmp_path):
        service = DashboardService(_settings(tmp_path), [_target()])
        assert service._bucket_for({"container": "abraham-trading"}) == "abraham"
        assert service._bucket_for({"container": "store-crawler"}) == "unassigned"

    def test_overview_counts(self, tmp_path):
        settings = _settings(tmp_path)
        state = StateStore(settings.state_path)
        state.record_finding("hash1", "fp1", "title")
        state.record_target_finding("abraham", "fp2", "title2")
        state.record_remediation("abraham", "fp2", "pr-opened", pr_url="http://pr")
        overview = DashboardService(settings, [_target()]).overview()
        assert overview["counts"] == {"findings": 2, "remediations": 1, "prs_opened": 1}
        assert overview["targets"][0]["id"] == "abraham"


@pytest.fixture()
def dashboard_server(tmp_path):
    settings = _settings(tmp_path)
    settings.findings_path.write_text(
        json.dumps({"title": "anomaly", "severity": "high"}) + "\n", encoding="utf-8"
    )
    server = make_server(settings, [_target()], "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class TestHTTPServer:
    def test_serves_index(self, dashboard_server):
        status, body = _get(dashboard_server + "/")
        assert status == 200
        assert b"Guilty Spark" in body

    def test_serves_findings_api(self, dashboard_server):
        status, body = _get(dashboard_server + "/api/findings")
        assert status == 200
        payload = json.loads(body)
        assert payload["findings"][0]["title"] == "anomaly"

    def test_anomalies_surface_loki_failure_as_json(self, dashboard_server):
        status, body = _get(dashboard_server + "/api/anomalies?minutes=5")
        assert status == 502
        assert "error" in json.loads(body)

    def test_unknown_path_404(self, dashboard_server):
        status, _ = _get(dashboard_server + "/nope.js")
        assert status == 404

    def test_no_path_traversal(self, dashboard_server):
        status, _ = _get(dashboard_server + "/..%2Fdashboard.py")
        assert status == 404
