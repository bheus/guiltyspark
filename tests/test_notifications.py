import json
import unittest
from unittest.mock import patch

from guiltyspark.models import Finding
from guiltyspark.notifications import RESEND_ENDPOINT, EmailNotifier


def finding() -> Finding:
    return Finding(
        fingerprint="abc123",
        title="Inventory worker crash loop",
        severity="high",
        summary="The worker exits on startup.",
        evidence=["panic: nil map"],
        suspected_cause="Unguarded map access.",
        recommended_fix="Initialize the map.",
        pr_recommended=True,
        raw={},
    )


class EmailNotifierTests(unittest.TestCase):
    def test_disabled_when_credentials_missing(self) -> None:
        notifier = EmailNotifier(api_key=None, sender="a@b.com", recipient="c@d.com")
        self.assertFalse(notifier.enabled)
        with patch("guiltyspark.notifications.urllib.request.urlopen") as urlopen:
            notifier.send_pr_opened(finding(), "https://pr", "owner/app")
        urlopen.assert_not_called()

    def test_disabled_when_credentials_blank(self) -> None:
        # The deployed failure mode: the var is wired through but empty.
        notifier = EmailNotifier(api_key="", sender="a@b.com", recipient="c@d.com")
        self.assertFalse(notifier.enabled)

    def test_send_posts_monitor_voiced_email_to_resend(self) -> None:
        notifier = EmailNotifier(
            api_key="re_key",
            sender="monitor@fleet.example",
            recipient="reclaimer@example.com",
        )
        self.assertTrue(notifier.enabled)
        with patch("guiltyspark.notifications.urllib.request.urlopen") as urlopen:
            notifier.send_pr_opened(finding(), "https://github.com/owner/app/pull/7", "owner/app")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, RESEND_ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer re_key")
        # Cloudflare (in front of Resend) 403s the default urllib UA — must be named.
        self.assertEqual(request.get_header("User-agent"), "guiltyspark/1.0")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["from"], "monitor@fleet.example")
        self.assertEqual(payload["to"], ["reclaimer@example.com"])
        self.assertIn("Reclaimer", payload["subject"])
        self.assertIn("Inventory worker crash loop", payload["subject"])
        self.assertIn("https://github.com/owner/app/pull/7", payload["text"])
        self.assertIn("owner/app", payload["text"])


if __name__ == "__main__":
    unittest.main()
