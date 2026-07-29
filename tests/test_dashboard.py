from __future__ import annotations

import json
import re
import threading
import urllib.request
from importlib import resources
from pathlib import Path

import pytest

from guiltyspark.config import Settings
from guiltyspark.dashboard import (
    ANOMALY_LEVELS,
    ANOMALY_LINE_PATTERN,
    DashboardService,
    _containers_param,
    _DECLARED_NON_ANOMALY_PATTERN,
    _merge_events,
    anomaly_queries,
    make_server,
    parse_stream_selector,
    selector_matches,
    tail_findings,
    validate_pattern,
    with_label_filter,
)
from guiltyspark.models import Incident, LogEvent
from guiltyspark.state import StateStore
from guiltyspark.targets import Target


def _wait_for_clustering(service, timeout: float = 5.0) -> None:
    """Block until the background clustering worker has published (or cleared)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with service._cluster_lock:
            if not service._cluster_running:
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


class TestWithLabelFilter:
    def test_appends_to_existing_matchers(self):
        assert (
            with_label_filter('{job=~".+"}', "container", ["abraham"])
            == '{job=~".+", container=~"abraham"}'
        )

    def test_multiple_values_join_as_alternation(self):
        assert (
            with_label_filter('{job=~".+"}', "container", ["a", "b"])
            == '{job=~".+", container=~"a|b"}'
        )

    def test_regex_metacharacters_are_escaped(self):
        result = with_label_filter('{job=~".+"}', "container", ["app.v1+x"])
        assert result == '{job=~".+", container=~"app\\\\.v1\\\\+x"}'

    def test_empty_selector_gets_no_leading_comma(self):
        assert with_label_filter("{}", "container", ["a"]) == '{container=~"a"}'

    def test_no_values_is_a_noop(self):
        assert with_label_filter('{job=~".+"}', "container", []) == '{job=~".+"}'

    def test_no_selector_is_a_noop(self):
        assert with_label_filter("count_over_time", "container", ["a"]) == "count_over_time"

    def test_pipeline_is_preserved(self):
        assert (
            with_label_filter('{job="d"} |= "err"', "container", ["a"])
            == '{job="d", container=~"a"} |= "err"'
        )


class TestAnomalyQueries:
    def test_covers_both_line_and_label_severity_rules(self):
        queries = anomaly_queries('{job=~".+"}')
        assert queries == [
            f'{{job=~".+"}} |~ {json.dumps(ANOMALY_LINE_PATTERN)} '
            f"!~ {json.dumps(_DECLARED_NON_ANOMALY_PATTERN)}",
            '{job=~".+", level=~"(?i)^(error|fatal)$"}',
            '{job=~".+", severity=~"(?i)^(error|fatal)$"}',
        ]

    def test_keyword_query_excludes_lines_declaring_a_benign_level(self):
        # Without this, Loki's own info-level log of the query below matches the
        # query's keywords, so polling the dashboard manufactures anomalies.
        keyword_query = anomaly_queries('{job=~".+"}')[0]
        assert f"!~ {json.dumps(_DECLARED_NON_ANOMALY_PATTERN)}" in keyword_query

    def test_prefilter_drops_the_lines_log_event_level_would_call_benign(self):
        # The prefilter exists to avoid fetching lines LogEvent.level will
        # discard, so it must recognize every serialization LogEvent.level does.
        pattern = re.compile(_DECLARED_NON_ANOMALY_PATTERN)
        for line in [
            'level=info msg="error saving"',
            '{"level": "INFO", "message": "fetch complete (0 errors)"}',
            '{"levelname":"INFO","msg":"error saving"}',
        ]:
            assert LogEvent(ts_ns=1, labels={}, line=line).level not in ANOMALY_LEVELS
            assert pattern.search(line), line

    def test_prefilter_keeps_lines_that_declare_an_anomaly(self):
        pattern = re.compile(_DECLARED_NON_ANOMALY_PATTERN)
        for line in [
            'level=error msg="upstream refused"',
            '{"level": "ERROR", "message": "upstream refused"}',
        ]:
            assert LogEvent(ts_ns=1, labels={}, line=line).level in ANOMALY_LEVELS
            assert not pattern.search(line), line

    def test_a_served_query_log_is_not_an_anomaly(self):
        # End to end over the loop's actual shape: Loki logs the query text at
        # info; LogEvent.level must not read the echoed keywords as an anomaly.
        served = (
            'level=info ts=2026-07-15T15:32:09Z caller=metrics.go:159 '
            f'query="{anomaly_queries(chr(123) + chr(125))[0]}"'
        )
        assert LogEvent(ts_ns=1, labels={}, line=served).level not in ANOMALY_LEVELS

    def test_container_filter_survives_into_every_variant(self):
        base = with_label_filter('{job=~".+"}', "container", ["abraham"])
        assert all('container=~"abraham"' in query for query in anomaly_queries(base))

    def test_matches_what_log_event_level_calls_an_anomaly(self):
        # The line-pattern query must match every line LogEvent.level would call
        # error/fatal on the basis of its text alone.
        pattern = re.compile(ANOMALY_LINE_PATTERN)
        for line in [
            "kernel panic",
            "FATAL: out of memory",
            "Error: connection refused",
            "unhandled exception",
            "Traceback (most recent call last):",
        ]:
            assert LogEvent(ts_ns=1, labels={}, line=line).level in {"error", "fatal"}
            assert pattern.search(line), line

    def test_does_not_prefilter_away_non_anomalies_only(self):
        # An "info" line is allowed to be fetched, but must not be a false
        # negative for anything LogEvent.level rates as an anomaly.
        assert LogEvent(ts_ns=1, labels={}, line="request served").level == "info"
        assert not re.compile(ANOMALY_LINE_PATTERN).search("request served")


class TestMergeEvents:
    def _event(self, ts, line="error boom", **labels):
        return LogEvent(ts_ns=ts, labels=labels or {"container": "a"}, line=line)

    def test_overlapping_queries_do_not_double_count(self):
        event = self._event(1)
        merged = _merge_events([[event], [event], []])
        assert len(merged) == 1

    def test_genuine_repeats_of_a_line_are_kept(self):
        # The same line twice at the same instant is real volume, not overlap.
        page = [self._event(1), self._event(1)]
        merged = _merge_events([page, [self._event(1)]])
        assert len(merged) == 2

    def test_union_keeps_lines_only_one_query_found(self):
        by_line = self._event(1, line="error boom")
        by_label = self._event(2, line="connection refused", level="error")
        merged = _merge_events([[by_line], [by_label]])
        assert [event.line for event in merged] == ["error boom", "connection refused"]

    def test_result_is_ordered_by_timestamp(self):
        merged = _merge_events([[self._event(30)], [self._event(10), self._event(20)]])
        assert [event.ts_ns for event in merged] == [10, 20, 30]


class TestContainersParam:
    def test_comma_separated_and_repeated(self):
        assert _containers_param({"container": ["a,b", "c"]}) == ["a", "b", "c"]

    def test_dedupes_and_strips(self):
        assert _containers_param({"container": [" a , a ", "a"]}) == ["a"]

    def test_missing_param(self):
        assert _containers_param({}) == []

    def test_caps_entry_count(self):
        raw = ",".join(f"c{i}" for i in range(80))
        assert len(_containers_param({"container": [raw]})) == 50

    def test_drops_oversized_values(self):
        assert _containers_param({"container": ["x" * 500 + ",ok"]}) == ["ok"]


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

            def query_window(self, *args, **kwargs):
                return events, False

            def count_over_window(self, *args, **kwargs):
                return len(events)

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

    def test_container_filter_narrows_loki_query(self, tmp_path, monkeypatch):
        import guiltyspark.dashboard as dashboard

        seen_queries: list[str] = []

        class FakeLoki:
            def __init__(self, *args, **kwargs):
                pass

            def query_window(self, *args, **kwargs):
                seen_queries.append(kwargs["query"])
                return [], False

            def count_over_window(self, query, *args, **kwargs):
                seen_counts.append(query)
                return 0

        seen_counts: list[str] = []
        monkeypatch.setattr(dashboard, "LokiClient", FakeLoki)
        service = DashboardService(_settings(tmp_path), [_target()])

        unfiltered = service.anomalies(minutes=60)
        unfiltered_queries = list(seen_queries)
        seen_queries.clear()
        filtered = service.anomalies(minutes=60, containers=["homebridge", "abraham"])

        # Every severity variant carries the container matcher, so the filter is
        # applied by Loki rather than after the fact.
        assert all('job=~".+"' in query for query in unfiltered_queries)
        assert all(
            'container=~"homebridge|abraham"' in query for query in seen_queries
        )
        # The denominator is counted over the unfiltered-by-severity selector.
        assert seen_counts == [
            '{job=~".+"}',
            '{job=~".+", container=~"homebridge|abraham"}',
        ]
        assert unfiltered["containers"] == []
        assert filtered["containers"] == ["homebridge", "abraham"]

    def test_containers_lists_label_values(self, tmp_path, monkeypatch):
        import guiltyspark.dashboard as dashboard

        class FakeLoki:
            def __init__(self, *args, **kwargs):
                pass

            def label_values(self, label, start_ns, end_ns):
                assert label == "container"
                return ["abraham", "homebridge"]

        monkeypatch.setattr(dashboard, "LokiClient", FakeLoki)
        service = DashboardService(_settings(tmp_path), [_target()])

        result = service.containers(minutes=60)
        assert result["label"] == "container"
        assert result["containers"] == ["abraham", "homebridge"]

    def _fake_loki(self, monkeypatch, events):
        import guiltyspark.dashboard as dashboard

        class FakeLoki:
            def __init__(self, *args, **kwargs):
                pass

            def query_window(self, *args, **kwargs):
                return events, False

            def count_over_window(self, *args, **kwargs):
                return len(events)

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

    def _incident(self, n: int) -> Incident:
        return Incident(
            fingerprint=f"fp{n}",
            service="loki",
            level="error",
            first_seen_ns=n,
            last_seen_ns=n,
            count=1,
            labels={"container": "loki"},
            samples=[f"error {n}"],
        )

    def test_churning_anomaly_set_never_clusters_concurrently(
        self, tmp_path, monkeypatch
    ):
        # A busy installation changes its anomaly set on most polls. Each change
        # used to spawn its own worker, stacking one `codex exec` per poll on the
        # host; only one may ever be in flight.
        import guiltyspark.dashboard as dashboard
        from guiltyspark.clustering import AnomalyGroup

        entered = threading.Event()
        release = threading.Event()
        counter_lock = threading.Lock()
        live = {"now": 0, "max": 0, "calls": 0}

        def fake_cluster(settings, incidents):
            with counter_lock:
                live["now"] += 1
                live["calls"] += 1
                live["max"] = max(live["max"], live["now"])
            entered.set()
            assert release.wait(5), "test deadlock"
            with counter_lock:
                live["now"] -= 1
            return [
                AnomalyGroup(
                    title="loki",
                    summary="same class",
                    fingerprints=[i.fingerprint for i in incidents],
                )
            ]

        monkeypatch.setattr(dashboard, "cluster_incidents", fake_cluster)
        service = DashboardService(
            _settings(tmp_path, dashboard_grouping=True), [_target()]
        )

        service._cluster_cached([self._incident(0)])
        assert entered.wait(5), "worker never started clustering"
        # Ten further polls, each a different set, while Codex is still working.
        for n in range(1, 11):
            service._cluster_cached([self._incident(n)])
        release.set()
        _wait_for_clustering(service)

        assert live["max"] == 1
        # The churn collapses onto the newest set rather than clustering each:
        # the in-flight pass, then one more for whatever landed meanwhile.
        assert live["calls"] == 2
        assert service._cluster_sig == ("fp10",)

    def test_failed_clustering_retires_the_worker_for_a_later_retry(
        self, tmp_path, monkeypatch
    ):
        # A Codex outage must not leave the worker flag stuck, or clustering
        # would never be attempted again for the life of the process.
        import guiltyspark.dashboard as dashboard

        def boom(*_a, **_k):
            raise RuntimeError("codex unavailable")

        monkeypatch.setattr(dashboard, "cluster_incidents", boom)
        service = DashboardService(
            _settings(tmp_path, dashboard_grouping=True), [_target()]
        )
        service._cluster_cached([self._incident(0)])
        _wait_for_clustering(service)

        with service._cluster_lock:
            assert service._cluster_running is False
            assert service._cluster_wanted is None

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

    def test_promotion_can_explicitly_release_observed_anomalies(self, tmp_path):
        service = DashboardService(_settings(tmp_path), [_target()])
        service.state.enqueue_remediation_job(
            "abraham", "observed-fp", '{"case": 1}', status="held"
        )
        listed = service.list_targets()["targets"][0]
        assert listed["held_remediations"] == 1

        promoted = dict(listed)
        promoted.update(
            mode="pr",
            test_commands=["pytest"],
            allowed_paths=["src", "tests"],
            release_observed=True,
        )
        result = service.save_target(promoted)

        assert result["released_remediations"] == 1
        assert service.state.held_remediation_jobs("abraham") == 0
        assert len(service.state.pending_remediation_jobs("abraham")) == 1

    def test_release_requires_an_active_protocol(self, tmp_path):
        service = DashboardService(_settings(tmp_path), [_target()])
        payload = service.list_targets()["targets"][0]
        payload["release_observed"] = True

        with pytest.raises(ValueError, match="active protocol"):
            service.save_target(payload)

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
def built_bundle():
    """Fabricate a minimal dashboard bundle so the static-serving tests do not
    depend on a real `npm run build`. src/guiltyspark/web is git-ignored build
    output that may be absent; we create just enough (index.html + one hashed
    asset) and remove only what we added."""
    web = Path(str(resources.files("guiltyspark").joinpath("web")))
    assets = web / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    index = web / "index.html"
    asset = assets / "index-test.js"
    created = []
    if not index.exists():
        index.write_text(
            '<!doctype html><meta charset="utf-8"><title>343 Guilty Spark</title>'
            '<script type="module" src="./assets/index-test.js"></script>',
            encoding="utf-8",
        )
        created.append(index)
    if not asset.exists():
        asset.write_text("console.log('monitor online');\n", encoding="utf-8")
        created.append(asset)
    yield
    for path in created:
        path.unlink(missing_ok=True)


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
    def test_serves_index(self, built_bundle, dashboard_server):
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

    def test_serves_hashed_bundle_asset(self, built_bundle, dashboard_server):
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
