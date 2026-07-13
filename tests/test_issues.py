from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from guiltyspark.clustering import AnomalyGroup
from guiltyspark.config import Settings
from guiltyspark.issues import plan_remediations
from guiltyspark.models import Finding, Incident


def settings(**over) -> Settings:
    base = Settings.from_env()
    return base.__class__(**{**base.__dict__, **over})


def incident(fp: str, count: int = 5, sample: str = "boom") -> Incident:
    return Incident(
        fingerprint=fp,
        service="abraham",
        level="error",
        first_seen_ns=0,
        last_seen_ns=0,
        count=count,
        labels={},
        samples=[sample],
    )


def finding(fp: str, severity: str = "high") -> Finding:
    return Finding(
        fingerprint=fp,
        title=f"issue {fp}",
        severity=severity,
        summary="s",
        evidence=[],
        suspected_cause="c",
        recommended_fix="f",
        pr_recommended=True,
        raw={},
    )


class FakeState:
    """In-memory stand-in for the parts of StateStore the planner touches."""

    def __init__(self) -> None:
        self.members: dict[str, str] = {}
        self.issues: dict[str, dict] = {}
        self.last_pr: dict[str, dict] = {}  # issue_key -> {pr_url, status, created_at}

    def issue_for_fingerprint(self, target_id, fp):
        return self.members.get(fp)

    def record_issue_member(self, target_id, fp, issue_key):
        self.members.setdefault(fp, issue_key)

    def create_issue(self, target_id, issue_key, title, service, anchor_fp, sample):
        self.issues.setdefault(issue_key, {"title": title, "service": service})

    def active_issues(self, target_id, within_seconds, limit=40):
        return [
            {
                "issue_key": k,
                "title": v["title"],
                "service": v["service"],
                "anchor_fingerprint": k,
                "anchor_sample": "boom",
            }
            for k, v in self.issues.items()
        ]

    def issue_last_pr(self, target_id, issue_key):
        return self.last_pr.get(issue_key)


class FakePrStatus:
    def __init__(self, state="merged"):
        self._state = state

    def status(self, pr_url):
        if not pr_url:
            return None
        return {"state": self._state}


def one_cluster(_settings, incidents):
    """All incidents collapse into a single issue."""
    return [AnomalyGroup("cash overspend", "same bug", [i.fingerprint for i in incidents])]


class PlanRemediationsTests(unittest.TestCase):
    def test_near_duplicates_collapse_to_one_pr(self) -> None:
        st = FakeState()
        candidates = [
            (finding("aaa", "medium"), incident("aaa", count=3)),
            (finding("bbb", "high"), incident("bbb", count=9)),
        ]
        with patch("guiltyspark.issues.cluster_incidents", one_cluster):
            plan = plan_remediations(settings(), st, "abraham", candidates)
        # One representative only, and it is the higher-count member.
        self.assertEqual(len(plan.to_remediate), 1)
        self.assertEqual(plan.to_remediate[0][1].fingerprint, "bbb")
        self.assertEqual(plan.suppressed, [])
        # Both fingerprints now belong to the same issue.
        self.assertEqual(len(set(st.members.values())), 1)

    def test_open_pr_blocks_refile(self) -> None:
        st = FakeState()
        st.members["ccc"] = "issue-1"
        st.issues["issue-1"] = {"title": "t", "service": "abraham"}
        st.last_pr["issue-1"] = {
            "pr_url": "https://github.com/o/r/pull/1",
            "status": "pr-opened",
            "created_at": "2000-01-01 00:00:00",  # ancient, but PR still open
        }
        candidates = [(finding("ccc"), incident("ccc"))]
        plan = plan_remediations(
            settings(), st, "abraham", candidates, pr_status=FakePrStatus("open")
        )
        self.assertEqual(plan.to_remediate, [])
        self.assertEqual(len(plan.suppressed), 1)
        self.assertEqual(plan.suppressed[0].reason, "open-pr")

    def test_merged_within_cooldown_blocks_refile(self) -> None:
        st = FakeState()
        st.members["ddd"] = "issue-2"
        st.issues["issue-2"] = {"title": "t", "service": "abraham"}
        recent = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 60))
        st.last_pr["issue-2"] = {
            "pr_url": "https://github.com/o/r/pull/2",
            "status": "pr-opened",
            "created_at": recent,
        }
        candidates = [(finding("ddd"), incident("ddd"))]
        plan = plan_remediations(
            settings(), st, "abraham", candidates, pr_status=FakePrStatus("merged")
        )
        self.assertEqual(plan.to_remediate, [])
        self.assertEqual(plan.suppressed[0].reason, "cooldown")

    def test_merged_beyond_cooldown_refiles(self) -> None:
        st = FakeState()
        st.members["eee"] = "issue-3"
        st.issues["issue-3"] = {"title": "t", "service": "abraham"}
        st.last_pr["issue-3"] = {
            "pr_url": "https://github.com/o/r/pull/3",
            "status": "pr-opened",
            "created_at": "2000-01-01 00:00:00",
        }
        candidates = [(finding("eee"), incident("eee"))]
        plan = plan_remediations(
            settings(issue_cooldown_seconds=3600),
            st,
            "abraham",
            candidates,
            pr_status=FakePrStatus("merged"),
        )
        self.assertEqual(len(plan.to_remediate), 1)
        self.assertEqual(plan.suppressed, [])

    def test_dedup_disabled_keeps_fingerprints_separate(self) -> None:
        st = FakeState()
        candidates = [
            (finding("f1"), incident("f1")),
            (finding("f2"), incident("f2")),
        ]
        # cluster_incidents must not even be consulted when disabled.
        with patch("guiltyspark.issues.cluster_incidents", side_effect=AssertionError):
            plan = plan_remediations(
                settings(dedup_issues=False), st, "abraham", candidates
            )
        self.assertEqual(len(plan.to_remediate), 2)

    def test_codex_failure_falls_back_to_identity(self) -> None:
        st = FakeState()
        candidates = [
            (finding("g1"), incident("g1")),
            (finding("g2"), incident("g2")),
        ]
        with patch(
            "guiltyspark.issues.cluster_incidents", side_effect=RuntimeError("codex down")
        ):
            plan = plan_remediations(settings(), st, "abraham", candidates)
        # Fail-safe: no crash, each fingerprint remediated independently.
        self.assertEqual(len(plan.to_remediate), 2)


if __name__ == "__main__":
    unittest.main()
