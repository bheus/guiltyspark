import unittest
from pathlib import Path

from guiltyspark.targets import load_targets_json


class DeploymentProfileTests(unittest.TestCase):
    def test_all_target_profiles_are_valid(self) -> None:
        profiles = Path(__file__).parents[1] / "deploy" / "profiles"
        paths = sorted(profiles.glob("*.targets.json"))
        self.assertTrue(paths)

        for path in paths:
            with self.subTest(path=path.name):
                targets = load_targets_json(path.read_text(encoding="utf-8"))
                self.assertTrue(targets)
