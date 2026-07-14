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
    validate_pattern,
)
from guiltyspark.models import LogEvent
from guiltyspark.state import StateStore
from guiltyspark.targets import Target


def _wait_for_clustering(service, timeout: float = 5.0) -> None:
    """Block until the background clustering worker has published (or cleared)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with service._cluster_lock:
            if service._cluster_pending is None:
                return
        time.sleep(0.01)
    raise AssertionError("clustering worker did not finish within timeout")


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


class TestValidatePattern:
    def test_accepts_reasonable_regex(self):
        assert validate_pattern("  error .*scheduler  ") == "error .*scheduler"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            validate_pattern("   ")

    def test_rejects_too_broad(self):
        for degenerate in (".*", ".+", ".", "^.*$"):
            with pytest.raises(ValueError):
                validate_pattern(degenerate)

    def test_rejects_uncompilable(self):
        with pytest.raises(ValueError):
            validate_pattern("error (unterminated")

    def test_rejects_overlong(self):
        with pytest.raises(ValueError):
            validate_pattern("a" * 1001)


class TestTailFindings:
    def test_returns_newest_first_without_raw(self, tmp_path):
        path = tmp_path / "findings.jsonl"
        lines = [
            json.dumps({"title": f"finding {i}", "raw": {"noise": True}})
            for i in range(5)
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        found, total = tail_findings(path, limit=3)
        assert [item["title"] for item in found] == ["finding 4", "finding 3", "finding 2"]
        assert all("raw" not in item for item in found)
        assert total == 5

    def test_offset_pages_back_through_history(self, tmp_path):
        path = tmp_path / "findings.jsonl"
        lines = [json.dumps({"title": f"finding {i}"}) for i in range(5)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        page, total = tail_findings(path, limit=2, offset=2)
        assert [item["title"] for item in page] == ["finding 2", "finding 1"]
        assert total == 5
        # An offset past the end yields an empty page but the true total.
        tail, tail_total = tail_findings(path, limit=2, offset=10)
        assert tail == []
        assert tail_total == 5

    def test_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "findings.jsonl"
        path.write_text('not json\n{"title": "ok"}\n', encoding="utf-8")
        page, total = tail_findings(path, 10)
        assert [item["title"] for item in page] == ["ok"]
        assert total == 1

    def test_missing_file(self, tmp_path):
        assert tail_findings(tmp_path / "absent.jsonl", 10) == ([], 0)


class TestDashboardService:
    def test_bucket_classification(self, tmp_path):
        service = DashboardService(_settings(tmp_path), [_target()])
        assert service._bucket_for({"container": "abraham-trading"}) == "abraham"
        assert service._bucket_for({"container": "store-crawler"}) == "unassigned"

    def test_ignored_incidents_are_filtered(self, tmp_path, monkeypatch):
        import guiltyspark.dashboard as dashboard

        events = [
            LogEvent(ts_ns=1, labels={"container": "noisy"}, line="error boom"),
            LogEvent(ts_ns=2, labels={"container": "noisy"}, line="error boom"),
        ]

        class FakeLoki:
            def __init__(self, *args, **kwargs):
                pass

            def query_range(self, *args, **kwargs):
                return events

        monkeypatch.setattr(dashboard, "LokiClient", FakeLoki)
        service = DashboardService(_settings(tmp_path), [_target()])

        first = service.anomalies(minutes=60)
        assert len(first["incidents"]) == 1
        assert first["ignored_count"] == 0
        fingerprint = first["incidents"][0]["fingerprint"]

        service.state.ignore_anomaly(fingerprint, "noise")
        second = service.anomalies(minutes=60)
        assert second["incidents"] == []
        assert second["ignored_count"] == 1

    def _fake_loki(self, monkeypatch, events):
        import guiltyspark.dashboard as dashboard

        class FakeLoki:
            def __init__(self, *args, **kwargs):
                pass

            def query_range(self, *args, **kwargs):
                return events

        monkeypatch.setattr(dashboard, "LokiClient", FakeLoki)

    def test_grouping_absent_when_disabled(self, tmp_path, monkeypatch):
        events = [
            LogEvent(ts_ns=1, labels={"container": "loki"}, line="error alpha"),
            LogEvent(ts_ns=2, labels={"container": "loki"}, line="error beta"),
        ]
        self._fake_loki(monkeypatch, events)
        service = DashboardService(_settings(tmp_path), [_target()])
        result = service.anomalies(minutes=60)
        assert "groups" not in result
        # Grouping disabled must not advertise a pending state.
        assert "groups_pending" not in result

    def test_grouping_pending_flag_until_worker_publishes(
        self, tmp_path, monkeypatch
    ):
        import guiltyspark.dashboard as dashboard
        from guiltyspark.clustering import AnomalyGroup

        events = [
            LogEvent(ts_ns=1, labels={"container": "loki"}, line="error alpha"),
            LogEvent(ts_ns=2, labels={"container": "loki"}, line="error beta"),
        ]
        self._fake_loki(monkeypatch, events)

        def fake_cluster(settings, incidents):
            return [
                AnomalyGroup(
                    title="loki subsystem",
                    summary="same class",
                    fingerprints=[i.fingerprint for i in incidents],
                )
            ]

        monkeypatch.setattr(dashboard, "cluster_incidents", fake_cluster)
        service = DashboardService(
            _settings(tmp_path, dashboard_grouping=True), [_target()]
        )

        # First poll: worker dispatched, no groups yet, pending flag set.
        first = service.anomalies(minutes=60)
        assert "groups" not in first
        assert first["groups_pending"] is True

        _wait_for_clustering(service)

        # Once published, groups are served and the pending flag is gone.
        second = service.anomalies(minutes=60)
        assert second["groups"]
        assert "groups_pending" not in second

    def test_grouping_clusters_and_caches(self, tmp_path, monkeypatch):
        import guiltyspark.dashboard as dashboard
        from guiltyspark.clustering import AnomalyGroup

        events = [
            LogEvent(ts_ns=1, labels={"container": "loki"}, line="error alpha"),
            LogEvent(ts_ns=2, labels={"container": "loki"}, line="error beta"),
        ]
        self._fake_loki(monkeypatch, events)
        service = DashboardService(
            _settings(tmp_path, dashboard_grouping=True), [_target()]
        )

        calls = {"n": 0}

        def fake_cluster(settings, incidents):
            calls["n"] += 1
            return [
                AnomalyGroup(
                    title="loki subsystem",
                    summary="same class",
                    fingerprints=[i.fingerprint for i in incidents],
                )
            ]

        # Patch before the first poll so the background worker never touches the
        # real (Codex-backed) clusterer.
        monkeypatch.setattr(dashboard, "cluster_incidents", fake_cluster)

        # Clustering runs off the request path: the first poll only kicks off the
        # background worker and returns the flat list (no groups yet).
        first = service.anomalies(minutes=60)
        assert "groups" not in first
        fps = sorted(i["fingerprint"] for i in first["incidents"])
        _wait_for_clustering(service)
        assert calls["n"] == 1

        # A later poll, after the worker published, serves the cached clusters.
        second = service.anomalies(minutes=60)
        groups = second["groups"]
        assert len(groups) == 1
        group = groups[0]
        assert group["title"] == "loki subsystem"
        assert group["count"] == 2
        assert sorted(group["fingerprints"]) == fps
        assert len(group["members"]) == 2

        # Same fingerprint set on the next refresh reuses the cached clustering.
        service.anomalies(minutes=60)
        assert calls["n"] == 1

    def test_grouping_degrades_on_codex_failure(self, tmp_path, monkeypatch):
        import guiltyspark.dashboard as dashboard

        events = [
            LogEvent(ts_ns=1, labels={"container": "loki"}, line="error alpha"),
            LogEvent(ts_ns=2, labels={"container": "loki"}, line="error beta"),
        ]
        self._fake_loki(monkeypatch, events)

        def boom(*_a, **_k):
            raise RuntimeError("codex unavailable")

        monkeypatch.setattr(dashboard, "cluster_incidents", boom)
        service = DashboardService(
            _settings(tmp_path, dashboard_grouping=True), [_target()]
        )
        result = service.anomalies(minutes=60)
        assert "groups" not in result
        assert len(result["incidents"]) == 2

    def test_rule_suppresses_matching_incidents_before_clustering(
        self, tmp_path, monkeypatch
    ):
        import guiltyspark.dashboard as dashboard

        events = [
            LogEvent(ts_ns=1, labels={"container": "loki"}, line="error notifying scheduler"),
            LogEvent(ts_ns=2, labels={"container": "loki"}, line="error querying storage"),
        ]
        self._fake_loki(monkeypatch, events)

        def fail_cluster(*_a, **_k):
            raise AssertionError("suppressed incidents must not be clustered")

        # Grouping enabled, but the only survivor is a single incident, which
        # skips Codex entirely — so clustering must never see the scheduler line.
        monkeypatch.setattr(dashboard, "cluster_incidents", fail_cluster)
        service = DashboardService(
            _settings(tmp_path, dashboard_grouping=True), [_target()]
        )
        service.add_ignore_rule("loki", r"error .*scheduler", "flap")

        result = service.anomalies(minutes=60)
        lines = [i["samples"][0] for i in result["incidents"]]
        assert lines == ["error querying storage"]
        assert result["ignored_count"] == 1

    def test_rule_service_scope_is_respected(self, tmp_path, monkeypatch):
        events = [
            LogEvent(ts_ns=1, labels={"container": "loki"}, line="error connection reset"),
            LogEvent(ts_ns=2, labels={"container": "other"}, line="error connection reset"),
        ]
        self._fake_loki(monkeypatch, events)
        service = DashboardService(_settings(tmp_path), [_target()])
        service.add_ignore_rule("loki", r"connection reset", "")

        result = service.anomalies(minutes=60)
        services = {i["service"] for i in result["incidents"]}
        assert services == {"other"}  # loki suppressed, other kept

    def test_suggest_pattern_validates_proposal(self, tmp_path, monkeypatch):
        import guiltyspark.dashboard as dashboard

        service = DashboardService(_settings(tmp_path), [_target()])

        monkeypatch.setattr(
            dashboard,
            "propose_pattern",
            lambda *_a, **_k: {"pattern": "error .*scheduler", "explanation": "safe"},
        )
        good = service.suggest_pattern("loki", ["error notifying scheduler"])
        assert good["pattern"] == "error .*scheduler"
        assert good["warning"] == ""

        # A too-broad proposal is surfaced with a warning, not silently applied.
        monkeypatch.setattr(
            dashboard, "propose_pattern", lambda *_a, **_k: {"pattern": ".*"}
        )
        risky = service.suggest_pattern("loki", ["boom"])
        assert risky["warning"]

    def test_add_ignore_rule_rejects_bad_pattern(self, tmp_path):
        service = DashboardService(_settings(tmp_path), [_target()])
        with pytest.raises(ValueError):
            service.add_ignore_rule("loki", ".*", "")

    def test_edited_targets_reflect_live(self, tmp_path):
        service = DashboardService(_settings(tmp_path), [_target()])
        service.save_target(
            {
                "id": "store",
                "loki_url": "http://loki.invalid:3100",
                "loki_query": '{container="store-crawler"}',
                "github_repo": "owner/store",
            }
        )
        assert service._bucket_for({"container": "store-crawler"}) == "store"
        service.delete_target("store")
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


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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

    def test_serves_hashed_bundle_asset(self, dashboard_server):
        # The built index.html references a hashed asset under assets/; the
        # relaxed static handler must serve that nested path.
        import re

        status, body = _get(dashboard_server + "/")
        assert status == 200
        match = re.search(rb"(assets/[A-Za-z0-9._-]+\.js)", body)
        assert match, "index.html should reference a hashed assets/*.js bundle"
        status, _ = _get(f"{dashboard_server}/{match.group(1).decode()}")
        assert status == 200

    def test_rejects_assets_traversal(self, dashboard_server):
        status, _ = _get(dashboard_server + "/assets/..%2F..%2Fdashboard.py")
        assert status == 404

    def test_target_create_list_delete(self, dashboard_server):
        payload = {
            "id": "store",
            "loki_url": "http://loki.invalid:3100",
            "loki_query": '{container="store-crawler"}',
            "github_repo": "owner/store",
        }
        status, body = _request("POST", dashboard_server + "/api/targets", payload)
        assert status == 200
        assert body["target"]["id"] == "store"

        status, body = _get(dashboard_server + "/api/targets")
        ids = [t["id"] for t in json.loads(body)["targets"]]
        assert "store" in ids

        status, body = _request(
            "DELETE", dashboard_server + "/api/targets?id=store"
        )
        assert status == 200 and body["deleted"] is True

    def test_invalid_target_rejected(self, dashboard_server):
        status, body = _request(
            "POST",
            dashboard_server + "/api/targets",
            {"id": "broken", "loki_url": "http://x"},
        )
        assert status == 400
        assert "error" in body

    def test_ignore_captures_context_and_restore(self, dashboard_server):
        status, body = _request(
            "POST",
            dashboard_server + "/api/anomalies/ignore",
            {
                "fingerprint": "deadbeef",
                "note": "noise",
                "service": "store-crawler",
                "level": "error",
                "sample": "connection reset",
                "count": 17,
            },
        )
        assert status == 200 and body["ignored"] is True

        status, body = _get(dashboard_server + "/api/anomalies/ignored")
        entry = next(
            i for i in json.loads(body)["ignored"] if i["fingerprint"] == "deadbeef"
        )
        assert entry["service"] == "store-crawler"
        assert entry["level"] == "error"
        assert entry["sample"] == "connection reset"
        assert entry["count"] == 17

        status, body = _request(
            "DELETE", dashboard_server + "/api/anomalies/ignore?fingerprint=deadbeef"
        )
        assert status == 200 and body["restored"] is True

    def test_update_note(self, dashboard_server):
        _request(
            "POST",
            dashboard_server + "/api/anomalies/ignore",
            {"fingerprint": "cafe", "service": "abraham"},
        )
        status, body = _request(
            "POST",
            dashboard_server + "/api/anomalies/note",
            {"fingerprint": "cafe", "note": "triaged: known flake"},
        )
        assert status == 200 and body["updated"] is True

        status, body = _get(dashboard_server + "/api/anomalies/ignored")
        entry = next(
            i for i in json.loads(body)["ignored"] if i["fingerprint"] == "cafe"
        )
        assert entry["note"] == "triaged: known flake"
        # Context must survive a note-only update.
        assert entry["service"] == "abraham"

    def test_note_on_unknown_fingerprint_is_rejected(self, dashboard_server):
        status, _ = _request(
            "POST",
            dashboard_server + "/api/anomalies/note",
            {"fingerprint": "missing", "note": "x"},
        )
        assert status == 400

    def test_ignore_requires_fingerprint(self, dashboard_server):
        status, body = _request(
            "POST", dashboard_server + "/api/anomalies/ignore", {"note": "x"}
        )
        assert status == 400

    def test_ignore_batch_silences_many(self, dashboard_server):
        status, body = _request(
            "POST",
            dashboard_server + "/api/anomalies/ignore-batch",
            {
                "anomalies": [
                    {"fingerprint": "batch1", "service": "loki", "count": 10},
                    {"fingerprint": "batch2", "service": "loki", "count": 9},
                    {"fingerprint": "", "service": "skip"},
                ]
            },
        )
        assert status == 200 and body["ignored"] is True and body["count"] == 2

        status, body = _get(dashboard_server + "/api/anomalies/ignored")
        fingerprints = {i["fingerprint"] for i in json.loads(body)["ignored"]}
        assert {"batch1", "batch2"} <= fingerprints

    def test_rule_create_list_delete(self, dashboard_server):
        status, body = _request(
            "POST",
            dashboard_server + "/api/anomalies/rules",
            {
                "service": "loki",
                "pattern": r"error .*scheduler",
                "title": "Loki scheduler interruption",
                "note": "flap",
            },
        )
        assert status == 200 and body["created"] is True
        rule_id = body["id"]

        status, body = _get(dashboard_server + "/api/anomalies/ignored")
        rule = next(r for r in json.loads(body)["rules"] if r["id"] == rule_id)
        assert rule["service"] == "loki"
        assert rule["pattern"] == r"error .*scheduler"
        assert rule["title"] == "Loki scheduler interruption"

        status, body = _request(
            "POST",
            dashboard_server + "/api/anomalies/rules/metadata",
            {"id": rule_id, "title": "Known Loki scheduler flap", "note": "expected"},
        )
        assert status == 200 and body["updated"] is True

        status, body = _get(dashboard_server + "/api/anomalies/ignored")
        rule = next(r for r in json.loads(body)["rules"] if r["id"] == rule_id)
        assert rule["title"] == "Known Loki scheduler flap"
        assert rule["note"] == "expected"

        status, body = _request(
            "DELETE", dashboard_server + f"/api/anomalies/rules?id={rule_id}"
        )
        assert status == 200 and body["deleted"] is True

    def test_rule_rejects_broad_pattern(self, dashboard_server):
        status, body = _request(
            "POST",
            dashboard_server + "/api/anomalies/rules",
            {"service": "loki", "pattern": ".*"},
        )
        assert status == 400
