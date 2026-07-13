import unittest
from unittest.mock import patch

from guiltyspark.config import Settings
from guiltyspark.github_pr import PrStatusClient, classify_pr, parse_pr_url


class ParsePrUrlTests(unittest.TestCase):
    def test_parses_owner_repo_number(self) -> None:
        self.assertEqual(
            parse_pr_url("https://github.com/bheus/Abraham/pull/37"),
            ("bheus", "Abraham", 37),
        )

    def test_rejects_non_pr_urls(self) -> None:
        self.assertIsNone(parse_pr_url("https://github.com/bheus/Abraham/issues/5"))
        self.assertIsNone(parse_pr_url(""))
        self.assertIsNone(parse_pr_url(None))


class ClassifyPrTests(unittest.TestCase):
    def test_merged_wins_over_closed(self) -> None:
        self.assertEqual(classify_pr({"state": "closed", "merged_at": "2026-07-13T00:00:00Z"}), "merged")
        self.assertEqual(classify_pr({"state": "closed", "merged": True}), "merged")

    def test_closed_unmerged(self) -> None:
        self.assertEqual(classify_pr({"state": "closed", "merged_at": None}), "closed")

    def test_draft_and_open(self) -> None:
        self.assertEqual(classify_pr({"state": "open", "draft": True}), "draft")
        self.assertEqual(classify_pr({"state": "open"}), "open")


class PrStatusClientTests(unittest.TestCase):
    def _client(self) -> PrStatusClient:
        client = PrStatusClient(Settings.from_env())
        # Avoid real auth resolution in tests.
        client.auth.token = lambda required=True: "token"  # type: ignore[assignment]
        return client

    def test_non_pr_url_returns_none_without_fetching(self) -> None:
        client = self._client()
        with patch("guiltyspark.github_pr.urllib.request.urlopen") as urlopen:
            self.assertIsNone(client.status("https://example.com/not-a-pr"))
        urlopen.assert_not_called()

    def test_caches_within_ttl(self) -> None:
        client = self._client()
        payload = b'{"state": "open", "merged_at": null, "closed_at": null}'

        class Resp:
            def read(self) -> bytes:
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        with patch("guiltyspark.github_pr.urllib.request.urlopen", return_value=Resp()) as urlopen:
            first = client.status("https://github.com/o/r/pull/1")
            second = client.status("https://github.com/o/r/pull/1")
        self.assertEqual(first["state"], "open")
        self.assertEqual(second["state"], "open")
        urlopen.assert_called_once()  # second call served from cache

    def test_http_failure_resolves_to_unknown(self) -> None:
        import urllib.error

        client = self._client()
        with patch(
            "guiltyspark.github_pr.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None),
        ):
            status = client.status("https://github.com/o/r/pull/9")
        self.assertEqual(status["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
