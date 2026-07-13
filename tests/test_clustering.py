import json

from guiltyspark import clustering
from guiltyspark.clustering import (
    AnomalyGroup,
    _groups_from_payload,
    cluster_incidents,
)
from guiltyspark.models import Incident


def _incident(fp: str, service: str = "loki", line: str = "boom") -> Incident:
    return Incident(
        fingerprint=fp,
        service=service,
        level="error",
        first_seen_ns=1,
        last_seen_ns=2,
        count=1,
        labels={"service": service},
        samples=[line],
    )


class TestGroupsFromPayload:
    def test_merges_and_preserves_membership(self):
        incidents = [_incident("a"), _incident("b"), _incident("c")]
        payload = {
            "groups": [
                {"title": "scheduler", "summary": "s", "fingerprints": ["a", "b"]},
                {"title": "storage", "summary": "s2", "fingerprints": ["c"]},
            ]
        }
        groups = _groups_from_payload(payload, incidents)
        assert [g.fingerprints for g in groups] == [["a", "b"], ["c"]]
        assert groups[0].title == "scheduler"

    def test_unassigned_fingerprints_become_singletons(self):
        incidents = [_incident("a"), _incident("b"), _incident("c")]
        payload = {"groups": [{"title": "t", "fingerprints": ["a"]}]}
        groups = _groups_from_payload(payload, incidents)
        # b and c were omitted by the model -> one singleton each, input order.
        assert [g.fingerprints for g in groups] == [["a"], ["b"], ["c"]]

    def test_unknown_and_duplicate_fingerprints_are_dropped(self):
        incidents = [_incident("a"), _incident("b")]
        payload = {
            "groups": [
                {"title": "t", "fingerprints": ["a", "ghost", "a"]},
                {"title": "dupe", "fingerprints": ["a", "b"]},
            ]
        }
        groups = _groups_from_payload(payload, incidents)
        # 'ghost' is not a real fingerprint; 'a' cannot be claimed twice.
        assert [g.fingerprints for g in groups] == [["a"], ["b"]]

    def test_empty_group_title_falls_back_to_service(self):
        incidents = [_incident("a", service="abraham")]
        payload = {"groups": [{"title": "  ", "fingerprints": ["a"]}]}
        groups = _groups_from_payload(payload, incidents)
        assert groups[0].title == "abraham"


class TestClusterIncidents:
    def test_empty_returns_empty(self):
        assert cluster_incidents(object(), []) == []

    def test_single_incident_skips_codex(self, monkeypatch):
        called = False

        def boom(*_a, **_k):
            nonlocal called
            called = True
            raise AssertionError("codex must not run for a single incident")

        monkeypatch.setattr(clustering, "codex_exec", boom)
        groups = cluster_incidents(object(), [_incident("a")])
        assert groups == [AnomalyGroup("loki", "boom", ["a"])]
        assert called is False

    def test_calls_codex_and_parses(self, monkeypatch):
        payload = {"groups": [{"title": "t", "summary": "s", "fingerprints": ["a", "b"]}]}
        monkeypatch.setattr(
            clustering, "codex_exec", lambda *_a, **_k: json.dumps(payload)
        )
        groups = cluster_incidents(object(), [_incident("a"), _incident("b")])
        assert groups == [AnomalyGroup("t", "s", ["a", "b"])]
