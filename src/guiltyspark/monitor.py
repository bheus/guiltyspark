from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from guiltyspark.agent import Analyzer
from guiltyspark.config import Settings
from guiltyspark.grouping import group_incidents
from guiltyspark.loki import LokiClient
from guiltyspark.models import Finding, Incident
from guiltyspark.notifications import Notifier
from guiltyspark.remediation import Remediator
from guiltyspark.state import StateStore
from guiltyspark.targets import Target


@dataclass(frozen=True)
class RunSummary:
    events: int
    incidents: int
    findings: int
    start_ns: int
    end_ns: int
    target_id: str = "default"
    remediations: int = 0


class Monitor:
    def __init__(self, settings: Settings, target: Target | None = None) -> None:
        self.settings = settings
        self.target = target
        self.target_id = target.id if target else "default"
        self.state = StateStore(settings.state_path)
        self.loki = LokiClient(
            base_url=target.loki_url if target else settings.loki_url,
            bearer_token=settings.loki_bearer_token,
            basic_auth=settings.loki_basic_auth,
        )
        self.analyzer = Analyzer(settings)
        self.notifier = Notifier(settings.notify_webhook_url)
        self.remediator = Remediator(settings)

    async def run_once(self) -> RunSummary:
        end_ns = time.time_ns()
        start_ns = self.state.get_cursor_ns(self.target_id)
        if start_ns is None:
            start_ns = end_ns - (self.settings.lookback_seconds * 1_000_000_000)

        events = self.loki.query_range(
            query=self.target.loki_query if self.target else self.settings.loki_query,
            start_ns=start_ns,
            end_ns=end_ns,
            limit=self.settings.loki_limit,
        )
        incidents = group_incidents(events, min_events=self.settings.min_events)
        incidents = incidents[: self.settings.max_incidents_per_run]
        findings = await self.analyzer.analyze(incidents, self.target)
        new_findings = self._write_new_findings(findings)
        remediations = await self._remediate(findings, incidents)
        self.state.set_cursor_ns(end_ns, self.target_id)
        return RunSummary(
            events=len(events),
            incidents=len(incidents),
            findings=len(new_findings),
            start_ns=start_ns,
            end_ns=end_ns,
            target_id=self.target_id,
            remediations=remediations,
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

    def _write_new_findings(self, findings: list[Finding]) -> list[Finding]:
        self.settings.findings_path.parent.mkdir(parents=True, exist_ok=True)
        written: list[Finding] = []
        with self.settings.findings_path.open("a", encoding="utf-8") as handle:
            for finding in findings:
                finding_hash = finding.stable_hash()
                if self.target:
                    if self.state.has_target_finding(self.target_id, finding.fingerprint):
                        continue
                elif self.state.has_finding(finding_hash):
                    continue
                handle.write(finding.as_json_line() + "\n")
                if self.target:
                    self.state.record_target_finding(
                        self.target_id, finding.fingerprint, finding.title
                    )
                else:
                    self.state.record_finding(finding_hash, finding.fingerprint, finding.title)
                try:
                    self.notifier.send(finding)
                except Exception as exc:
                    print(f"notify_error finding={finding_hash} error={exc}", flush=True)
                written.append(finding)
        return written

    async def _remediate(
        self, findings: list[Finding], incidents: list[Incident]
    ) -> int:
        if self.target is None or self.target.mode == "observe":
            return 0
        incidents_by_fingerprint = {incident.fingerprint: incident for incident in incidents}
        for finding in findings:
            if not finding.pr_recommended:
                continue
            incident = incidents_by_fingerprint.get(finding.fingerprint)
            if incident is None:
                continue
            payload = json.dumps(
                {"incident": asdict(incident), "finding": asdict(finding)}, sort_keys=True
            )
            self.state.enqueue_remediation_job(
                self.target_id, finding.fingerprint, payload
            )

        attempted = 0
        jobs = self.state.pending_remediation_jobs(
            self.target_id, include_validated=self.target.mode == "draft-pr"
        )
        for fingerprint, payload_text in jobs:
            payload = json.loads(payload_text)
            incident = Incident(**payload["incident"])
            finding = Finding(**payload["finding"])
            result = await asyncio.to_thread(
                self.remediator.repair, self.target, incident, finding
            )
            self.state.record_remediation(
                self.target_id,
                finding.fingerprint,
                result.status,
                result.details[-8000:],
                branch=result.branch,
                pr_url=result.pr_url,
            )
            self.state.update_remediation_job(
                self.target_id,
                fingerprint,
                result.status,
                result.details[-8000:] if result.status == "failed" else "",
            )
            print(
                f"target={self.target_id} remediation={result.status} "
                f"fingerprint={finding.fingerprint} pr_url={result.pr_url or ''}",
                flush=True,
            )
            attempted += 1
        return attempted


class FleetMonitor:
    def __init__(self, settings: Settings, targets: list[Target]) -> None:
        self.settings = settings
        self.monitors = [Monitor(settings, target) for target in targets]

    async def run_once(self) -> list[RunSummary]:
        summaries: list[RunSummary] = []
        for monitor in self.monitors:
            summaries.append(await monitor.run_once())
        return summaries

    async def run_forever(self) -> None:
        while True:
            started = datetime.now(timezone.utc).isoformat()
            for monitor in self.monitors:
                try:
                    summary = await monitor.run_once()
                    print(
                        f"{started} target={summary.target_id} events={summary.events} "
                        f"incidents={summary.incidents} new_findings={summary.findings} "
                        f"remediations={summary.remediations}",
                        flush=True,
                    )
                except Exception as exc:
                    print(
                        f"{started} target={monitor.target_id} monitor_error={exc}", flush=True
                    )
            await asyncio.sleep(self.settings.interval_seconds)
