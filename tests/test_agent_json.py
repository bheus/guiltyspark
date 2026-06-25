import unittest

from guiltyspark.agent import _extract_json


class AgentJsonTests(unittest.TestCase):
    def test_extract_json_accepts_markdown_fenced_json(self) -> None:
        payload = _extract_json('```json\n{"findings": []}\n```')
        self.assertEqual(payload, {"findings": []})

    def test_extract_json_finds_object_inside_text(self) -> None:
        payload = _extract_json('Here is the result: {"findings": []}')
        self.assertEqual(payload, {"findings": []})
