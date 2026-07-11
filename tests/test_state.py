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
