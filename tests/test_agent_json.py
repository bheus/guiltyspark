import unittest

from guiltyspark.agent import Analyzer, _extract_json
from guiltyspark.config import Settings
from guiltyspark.models import Incident


class AgentJsonTests(unittest.TestCase):
    def test_extract_json_accepts_markdown_fenced_json(self) -> None:
        payload = _extract_json('```json\n{"findings": []}\n```')
        self.assertEqual(payload, {"findings": []})

    def test_extract_json_finds_object_inside_text(self) -> None:
        payload = _extract_json('Here is the result: {"findings": []}')
        self.assertEqual(payload, {"findings": []})


class AgentPromptTests(unittest.TestCase):
    def _incident(self) -> Incident:
        return Incident(
            fingerprint="fp",
            service="worker",
            level="error",
            first_seen_ns=0,
            last_seen_ns=0,
            count=3,
            labels={},
            samples=["boom"],
        )

    def test_expected_logs_are_injected_into_prompt(self) -> None:
        analyzer = Analyzer(Settings.from_env())
        prompt = analyzer._prompt(
            [self._incident()],
            target=None,
            expected_logs="yfinance cache warning is benign",
        )
        self.assertIn("BEGIN EXPECTED LOGS", prompt)
        self.assertIn("yfinance cache warning is benign", prompt)

    def test_prompt_omits_expected_block_when_absent(self) -> None:
        analyzer = Analyzer(Settings.from_env())
        prompt = analyzer._prompt([self._incident()], target=None, expected_logs=None)
        self.assertNotIn("BEGIN EXPECTED LOGS", prompt)
        # An empty/whitespace doc is treated as absent, too.
        blank = analyzer._prompt([self._incident()], target=None, expected_logs="   ")
        self.assertNotIn("BEGIN EXPECTED LOGS", blank)
