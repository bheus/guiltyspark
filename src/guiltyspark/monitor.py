from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from guiltyspark.agent import Analyzer
from guiltyspark.config import Settings
from guiltyspark.grouping import group_incidents
from guiltyspark.loki import LokiClient
from guiltyspark.models import Finding
from guiltyspark.notifications import Notifier
from guiltyspark.state import StateStore


@dataclass(frozen=True)
class RunSummary:
    events: int
    incidents: int
    findings: int
    start_ns: int
    end_ns: int


class Monitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = StateStore(settings.state_path)
        self.loki = LokiClient(
            base_url=settings.loki_url,
            bearer_token=settings.loki_bearer_token,
            basic_auth=settings.loki_basic_auth,
        )
        self.analyzer = Analyzer(settings)
        self.notifier = Notifier(settings.notify_webhook_url)

    async def run_once(self) -> RunSummary:
        end_ns = time.time_ns()
        start_ns = self.state.get_cursor_ns()
        if start_ns is None:
            start_ns = end_ns - (self.settings.lookback_seconds * 1_000_000_000)

        events = self.loki.query_range(
            query=self.settings.loki_query,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=self.settings.loki_limit,
        )
        incidents = group_incidents(events, min_events=self.settings.min_events)
        incidents = incidents[: self.settings.max_incidents_per_run]
        findings = await self.analyzer.analyze(incidents)
        written = self._write_new_findings(findings)
        self.state.set_cursor_ns(end_ns)
        return RunSummary(
            events=len(events),
            incidents=len(incidents),
            findings=written,
            start_ns=start_ns,
            end_ns=end_ns,
        )

    async def run_forever(self) -> None:
        while True:
            started = datetime.now(timezone.utc).isoformat()
            try:
                summary = await self.run_once()
                print(
                    f"{started} events={summary.events} incidents={summary.incidents} "
                    f"new_findings={summary.findings}",
                    flush=True,
                )
            except Exception as exc:
                print(f"{started} monitor_error={exc}", flush=True)
            await asyncio.sleep(self.settings.interval_seconds)

    def _write_new_findings(self, findings: list[Finding]) -> int:
        self.settings.findings_path.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self.settings.findings_path.open("a", encoding="utf-8") as handle:
            for finding in findings:
                finding_hash = finding.stable_hash()
                if self.state.has_finding(finding_hash):
                    continue
                handle.write(finding.as_json_line() + "\n")
                self.state.record_finding(finding_hash, finding.fingerprint, finding.title)
                try:
                    self.notifier.send(finding)
                except Exception as exc:
                    print(f"notify_error finding={finding_hash} error={exc}", flush=True)
                written += 1
        return written
