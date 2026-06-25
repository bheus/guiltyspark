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
