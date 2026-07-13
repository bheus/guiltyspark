import tempfile
import unittest
from pathlib import Path

from guiltyspark.state import StateStore
from guiltyspark.targets import (
    Target,
    load_targets,
    load_targets_from_store,
    load_targets_json,
    target_to_payload,
)


class TargetConfigTests(unittest.TestCase):
    def test_loads_multiple_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.toml"
            path.write_text(
                """
[[targets]]
id = "inventory"
loki_url = "http://loki:3100"
loki_query = '{container="inventory-worker"}'
github_repo = "example/inventory"
mode = "fix"
test_commands = ["pytest"]
allowed_paths = ["src", "tests"]

[[targets]]
id = "website"
loki_url = "http://loki:3100/"
loki_query = '{container="website"}'
github_repo = "example/website"
""",
                encoding="utf-8",
            )

            targets = load_targets(path)

        self.assertEqual([target.id for target in targets], ["inventory", "website"])
        self.assertEqual(targets[0].mode, "fix")
        self.assertEqual(targets[0].allowed_paths, ("src", "tests"))
        self.assertEqual(targets[1].loki_url, "http://loki:3100")
        self.assertEqual(targets[1].mode, "observe")

    def test_rejects_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.toml"
            path.write_text(
                """
[[targets]]
id = "same"
loki_url = "http://loki"
loki_query = "query"
github_repo = "owner/one"

[[targets]]
id = "same"
loki_url = "http://loki"
loki_query = "query"
github_repo = "owner/two"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unique"):
                load_targets(path)

    def test_fix_mode_requires_tests_and_allowed_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.toml"
            path.write_text(
                """
[[targets]]
id = "unsafe"
loki_url = "http://loki"
loki_query = "query"
github_repo = "owner/repo"
mode = "fix"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "test_commands"):
                load_targets(path)

    def test_loads_targets_from_json_environment_value(self) -> None:
        targets = load_targets_json(
            '[{"id":"worker","loki_url":"http://loki:3100",'
            '"loki_query":"{container=\\"worker\\"}",'
            '"github_repo":"example/worker"}]'
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].id, "worker")
        self.assertEqual(targets[0].mode, "observe")

    def test_payload_round_trips_through_from_dict(self) -> None:
        original = Target.from_dict(
            {
                "id": "worker",
                "loki_url": "http://loki:3100",
                "loki_query": '{container="worker"}',
                "github_repo": "example/worker",
                "mode": "pr",
                "test_commands": ["pytest"],
                "allowed_paths": ["src", "tests"],
                "max_changed_files": 5,
            }
        )
        rebuilt = Target.from_dict(target_to_payload(original))
        self.assertEqual(original, rebuilt)

    def test_load_from_store_skips_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.sqlite3")
            store.upsert_target(
                "good",
                {
                    "id": "good",
                    "loki_url": "http://loki",
                    "loki_query": "{a=1}",
                    "github_repo": "owner/good",
                },
            )
            # Missing required github_repo -> must be skipped, not fatal.
            store.upsert_target("bad", {"id": "bad", "loki_query": "{b=2}"})

            targets = load_targets_from_store(store)
            self.assertEqual([target.id for target in targets], ["good"])

    def test_pr_mode_is_supported(self) -> None:
        targets = load_targets_json(
            '[{"id":"worker","loki_url":"http://loki:3100",'
            '"loki_query":"{container=\\"worker\\"}",'
            '"github_repo":"example/worker","mode":"pr",'
            '"test_commands":["pytest"],"allowed_paths":["src","tests"]}]'
        )
        self.assertEqual(targets[0].mode, "pr")
