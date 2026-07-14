import base64
import json
import unittest
import urllib.error
from unittest.mock import patch

from guiltyspark.config import Settings
from guiltyspark.github_content import RepoDocClient
from guiltyspark.targets import Target


def _target(expected_logs_path: str = "docs/EXPECTED_LOGS.md") -> Target:
    return Target.from_dict(
        {
            "id": "worker",
            "loki_url": "http://loki:3100",
            "loki_query": '{container="worker"}',
            "github_repo": "example/worker",
            "expected_logs_path": expected_logs_path,
        }
    )


def _contents_response(text: str) -> bytes:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return json.dumps({"encoding": "base64", "content": encoded}).encode("utf-8")


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class RepoDocClientTests(unittest.TestCase):
    def _client(self) -> RepoDocClient:
        client = RepoDocClient(Settings.from_env())
        client.auth.token = lambda required=True: "token"  # type: ignore[assignment]
        return client

    def test_no_target_or_path_returns_none_without_fetching(self) -> None:
        client = self._client()
        with patch(
            "guiltyspark.github_content.urllib.request.urlopen"
        ) as urlopen:
            self.assertIsNone(client.expected_logs(None))
            self.assertIsNone(client.expected_logs(_target(expected_logs_path="")))
        urlopen.assert_not_called()

    def test_fetches_and_decodes_document(self) -> None:
        client = self._client()
        resp = _Resp(_contents_response("# Expected\nyfinance cache warning is benign"))
        with patch(
            "guiltyspark.github_content.urllib.request.urlopen", return_value=resp
        ) as urlopen:
            text = client.expected_logs(_target())
        self.assertIn("yfinance cache warning is benign", text)
        # The request targets the contents API at the target's base branch.
        url = urlopen.call_args.args[0].full_url
        self.assertIn("/repos/example/worker/contents/docs/EXPECTED_LOGS.md", url)
        self.assertIn("ref=main", url)

    def test_caches_within_ttl(self) -> None:
        client = self._client()
        resp = _Resp(_contents_response("benign"))
        with patch(
            "guiltyspark.github_content.urllib.request.urlopen", return_value=resp
        ) as urlopen:
            first = client.expected_logs(_target())
            second = client.expected_logs(_target())
        self.assertEqual(first, second)
        urlopen.assert_called_once()  # second served from cache

    def test_http_failure_resolves_to_none(self) -> None:
        client = self._client()
        with patch(
            "guiltyspark.github_content.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None),
        ):
            self.assertIsNone(client.expected_logs(_target()))

    def test_directory_listing_resolves_to_none(self) -> None:
        client = self._client()
        # The contents API returns a JSON array for a directory, not a file blob.
        resp = _Resp(json.dumps([{"name": "a"}]).encode("utf-8"))
        with patch(
            "guiltyspark.github_content.urllib.request.urlopen", return_value=resp
        ):
            self.assertIsNone(client.expected_logs(_target()))


if __name__ == "__main__":
    unittest.main()
