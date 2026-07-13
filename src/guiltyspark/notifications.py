from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from guiltyspark.models import Finding

RESEND_ENDPOINT = "https://api.resend.com/emails"
# Resend sits behind Cloudflare, which returns a 403 "error code: 1010" to the
# default Python-urllib signature. A named User-Agent clears that block; send it
# on every outbound request so notifications are not silently rejected.
USER_AGENT = "guiltyspark/1.0"


@dataclass(frozen=True)
class Notifier:
    webhook_url: str | None = None

    def send(self, finding: Finding) -> None:
        if not self.webhook_url:
            return
        body = json.dumps(
            {
                "title": finding.title,
                "severity": finding.severity,
                "summary": finding.summary,
                "suspected_cause": finding.suspected_cause,
                "recommended_fix": finding.recommended_fix,
                "pr_recommended": finding.pr_recommended,
                "evidence": finding.evidence,
                "fingerprint": finding.fingerprint,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            return


@dataclass(frozen=True)
class EmailNotifier:
    """Sends a Monitor-voiced email via Resend when guiltyspark opens a PR.

    This only ever fires from guiltyspark's own PR-creation path, so the operator
    is never notified about pull requests they open themselves.
    """

    api_key: str | None = None
    sender: str | None = None
    recipient: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.sender and self.recipient)

    def send_pr_opened(self, finding: Finding, pr_url: str, repository: str) -> None:
        if not self.enabled:
            return
        subject = (
            f"[GuiltySpark] Reclaimer, a corrective measure awaits your authorization "
            f"— {finding.title}"
        )
        text = (
            "Reclaimer,\n\n"
            "An operational anomaly was detected and classified. I have prepared the "
            "smallest corrective measure permitted by repository protocol and opened a "
            "pull request for your review.\n\n"
            f"Repository: {repository}\n"
            f"Severity:   {finding.severity}\n"
            f"Anomaly:    {finding.title}\n\n"
            f"Assessment:\n{finding.summary}\n\n"
            f"Suspected cause:\n{finding.suspected_cause}\n\n"
            f"Pull request: {pr_url}\n\n"
            "Final authorization remains yours, Reclaimer.\n\n"
            f"Incident designation: {finding.fingerprint}\n"
        )
        body = json.dumps(
            {
                "from": self.sender,
                "to": [self.recipient],
                "subject": subject,
                "text": text,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            RESEND_ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            return
