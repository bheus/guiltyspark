from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from guiltyspark.models import Finding


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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):
            return
