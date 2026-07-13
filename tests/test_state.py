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

            store.ignore_anomaly("fp1", "just noise")
            store.ignore_anomaly("fp2")
            self.assertEqual(store.ignored_fingerprints(), {"fp1", "fp2"})
            listed = store.list_ignored_anomalies()
            self.assertEqual({item["fingerprint"] for item in listed}, {"fp1", "fp2"})

            self.assertTrue(store.unignore_anomaly("fp1"))
            self.assertFalse(store.unignore_anomaly("fp1"))
            self.assertEqual(store.ignored_fingerprints(), {"fp2"})
