import tempfile
import unittest

from guiltyspark.state import StateStore


class StateStoreTests(unittest.TestCase):
    def test_state_store_tracks_cursor_and_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")

            self.assertIsNone(store.get_cursor_ns())
            store.set_cursor_ns(123)
            self.assertEqual(store.get_cursor_ns(), 123)

            self.assertFalse(store.has_finding("abc"))
            store.record_finding("abc", "fingerprint", "title")
            self.assertTrue(store.has_finding("abc"))

            self.assertIsNone(store.get_cursor_ns("inventory"))
            store.set_cursor_ns(456, "inventory")
            self.assertEqual(store.get_cursor_ns("inventory"), 456)

            self.assertFalse(store.has_target_finding("inventory", "incident"))
            store.record_target_finding("inventory", "incident", "title")
            self.assertTrue(store.has_target_finding("inventory", "incident"))

            self.assertFalse(store.has_completed_remediation("inventory", "incident"))
            store.record_remediation("inventory", "incident", "failed", "tests failed")
            self.assertFalse(store.has_completed_remediation("inventory", "incident"))
            store.record_remediation("inventory", "incident", "validated", "tests passed")
            self.assertTrue(store.has_completed_remediation("inventory", "incident"))

            store.enqueue_remediation_job("inventory", "retry-me", '{"case": 1}')
            self.assertEqual(
                store.pending_remediation_jobs("inventory"),
                [("retry-me", '{"case": 1}')],
            )
            store.update_remediation_job("inventory", "retry-me", "failed", "network")
            self.assertEqual(len(store.pending_remediation_jobs("inventory")), 1)
            store.update_remediation_job("inventory", "retry-me", "validated")
            self.assertEqual(store.pending_remediation_jobs("inventory"), [])
            self.assertEqual(
                len(store.pending_remediation_jobs("inventory", include_validated=True)), 1
            )

            store.enqueue_remediation_job(
                "inventory-held", "observed", '{"case": 2}', status="held"
            )
            self.assertEqual(store.held_remediation_jobs("inventory-held"), 1)
            self.assertEqual(store.release_held_remediation_jobs("inventory-held"), 1)
            self.assertEqual(store.held_remediation_jobs("inventory-held"), 0)
            self.assertEqual(
                store.pending_remediation_jobs("inventory-held", limit=1),
                [("observed", '{"case": 2}')],
            )

    def test_target_crud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            self.assertEqual(store.count_targets(), 0)

            store.upsert_target("web", {"id": "web", "loki_query": "{a=1}"})
            store.upsert_target("api", {"id": "api", "loki_query": "{b=2}"})
            self.assertEqual(store.count_targets(), 2)
            payloads = store.list_target_payloads()
            self.assertEqual([p["id"] for p in payloads], ["api", "web"])

            store.upsert_target("web", {"id": "web", "loki_query": "{a=9}"})
            self.assertEqual(store.count_targets(), 2)
            web = next(p for p in store.list_target_payloads() if p["id"] == "web")
            self.assertEqual(web["loki_query"], "{a=9}")

            self.assertTrue(store.delete_target("web"))
            self.assertFalse(store.delete_target("web"))
            self.assertEqual([p["id"] for p in store.list_target_payloads()], ["api"])

    def test_seed_targets_is_guarded_against_re_seeding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            seed = [{"id": "web", "loki_query": "{a=1}"}]

            self.assertTrue(store.seed_targets_if_empty(seed))
            self.assertEqual(store.count_targets(), 1)

            # An operator deletes the seeded target from the dashboard.
            store.delete_target("web")
            # A restart must not resurrect it.
            self.assertFalse(store.seed_targets_if_empty(seed))
            self.assertEqual(store.count_targets(), 0)

    def test_ignored_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            self.assertEqual(store.ignored_fingerprints(), set())

            store.ignore_anomaly(
                "fp1",
                note="just noise",
                service="store-crawler",
                level="error",
                sample="connection reset by peer",
                count=42,
            )
            store.ignore_anomaly("fp2")
            self.assertEqual(store.ignored_fingerprints(), {"fp1", "fp2"})

            listed = {item["fingerprint"]: item for item in store.list_ignored_anomalies()}
            self.assertEqual(set(listed), {"fp1", "fp2"})
            self.assertEqual(listed["fp1"]["service"], "store-crawler")
            self.assertEqual(listed["fp1"]["level"], "error")
            self.assertEqual(listed["fp1"]["sample"], "connection reset by peer")
            self.assertEqual(listed["fp1"]["count"], 42)
            self.assertEqual(listed["fp1"]["note"], "just noise")

            # Note-only update must not disturb captured context.
            self.assertTrue(store.set_ignored_note("fp1", "triaged: upstream flaps"))
            self.assertFalse(store.set_ignored_note("absent", "nope"))
            refreshed = next(
                i for i in store.list_ignored_anomalies() if i["fingerprint"] == "fp1"
            )
            self.assertEqual(refreshed["note"], "triaged: upstream flaps")
            self.assertEqual(refreshed["service"], "store-crawler")

            self.assertTrue(store.unignore_anomaly("fp1"))
            self.assertFalse(store.unignore_anomaly("fp1"))
            self.assertEqual(store.ignored_fingerprints(), {"fp2"})

    def test_ignore_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            self.assertEqual(store.list_ignore_rules(), [])

            rid = store.add_ignore_rule(
                "loki", r"error .*scheduler", "known flap", "Scheduler flap"
            )
            store.add_ignore_rule("", r"connection reset")
            rules = store.list_ignore_rules()
            self.assertEqual(len(rules), 2)
            first = next(r for r in rules if r["id"] == rid)
            self.assertEqual(first["service"], "loki")
            self.assertEqual(first["pattern"], r"error .*scheduler")
            self.assertEqual(first["note"], "known flap")
            self.assertEqual(first["title"], "Scheduler flap")

            self.assertTrue(
                store.set_ignore_rule_metadata(rid, "Known scheduler flap", "expected")
            )
            updated = next(r for r in store.list_ignore_rules() if r["id"] == rid)
            self.assertEqual(updated["title"], "Known scheduler flap")
            self.assertEqual(updated["note"], "expected")

            self.assertTrue(store.delete_ignore_rule(rid))
            self.assertFalse(store.delete_ignore_rule(rid))
            self.assertEqual(len(store.list_ignore_rules()), 1)

    def test_issue_registry_and_last_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            self.assertIsNone(store.issue_for_fingerprint("abraham", "fp1"))

            store.create_issue(
                "abraham", "iss1", "cash overspend", "abraham", "fp1", "boom"
            )
            store.record_issue_member("abraham", "fp1", "iss1")
            store.record_issue_member("abraham", "fp2", "iss1")
            self.assertEqual(store.issue_for_fingerprint("abraham", "fp2"), "iss1")

            active = store.active_issues("abraham", within_seconds=3600)
            self.assertEqual([i["issue_key"] for i in active], ["iss1"])

            # No PR yet for the issue.
            self.assertIsNone(store.issue_last_pr("abraham", "iss1"))

            # A PR recorded against a *member* fingerprint surfaces for the issue.
            store.record_remediation(
                "abraham", "fp2", "pr-opened", "ok",
                branch="b", pr_url="https://github.com/o/r/pull/9",
            )
            last = store.issue_last_pr("abraham", "iss1")
            self.assertEqual(last["pr_url"], "https://github.com/o/r/pull/9")

            # Idempotent membership: re-recording fp1 keeps its original issue.
            store.record_issue_member("abraham", "fp1", "other")
            self.assertEqual(store.issue_for_fingerprint("abraham", "fp1"), "iss1")

    def test_recent_remediations_paginates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            for i in range(5):
                store.record_remediation("web", f"fp{i}", "pr-opened", "ok")

            self.assertEqual(store.count_remediations(), 5)
            # Newest first (last inserted has the highest id).
            page1 = store.recent_remediations(limit=2, offset=0)
            self.assertEqual([r["fingerprint"] for r in page1], ["fp4", "fp3"])
            page2 = store.recent_remediations(limit=2, offset=2)
            self.assertEqual([r["fingerprint"] for r in page2], ["fp2", "fp1"])
            page3 = store.recent_remediations(limit=2, offset=4)
            self.assertEqual([r["fingerprint"] for r in page3], ["fp0"])

    def test_ignore_anomalies_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path

            store = StateStore(Path(tmp) / "state.sqlite3")
            applied = store.ignore_anomalies(
                [
                    {"fingerprint": "a", "service": "loki", "level": "error", "count": 10},
                    {"fingerprint": "b", "service": "loki", "count": 9},
                    {"fingerprint": "", "service": "skip"},  # dropped: no fingerprint
                    {"not_a": "dict"},  # dropped: no fingerprint
                ]
            )
            self.assertEqual(applied, 2)
            self.assertEqual(store.ignored_fingerprints(), {"a", "b"})

            # Empty input is a no-op, and re-silencing upserts rather than erroring.
            self.assertEqual(store.ignore_anomalies([]), 0)
            store.set_ignored_note("a", "keep me")
            self.assertEqual(
                store.ignore_anomalies([{"fingerprint": "a", "service": "loki"}]), 1
            )
            self.assertEqual(store.ignored_fingerprints(), {"a", "b"})
