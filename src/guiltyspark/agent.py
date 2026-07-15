from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guiltyspark.config import Settings
from guiltyspark.models import Finding, Incident
from guiltyspark.targets import Target


AGENT_INSTRUCTIONS = """You are GuiltySpark, an observability agent for application fleets.

You inspect Loki incident summaries and find real bugs, misconfigurations, reliability
risks, security issues, and worthwhile improvements. You are skeptical of noisy logs:
do not invent causes. Tie each finding to evidence from the supplied incidents.

If remediation mode is observe, report findings without recommending a PR unless the
evidence clearly identifies a repository defect. In fix, draft-pr, or pr mode, recommend
repair work only when the logs support a specific, testable code or configuration fix.
External outages alone are not code defects, but missing fallback or error handling can be.

If the repository documents expected or benign log patterns, honor that documentation:
incidents that clearly match a documented expected pattern are normal operation, not
anomalies. Do not report them as findings unless the evidence shows the documented
behavior is itself malfunctioning.

Return only JSON with this shape:
{
  "findings": [
    {
      "fingerprint": "incident fingerprint",
      "title": "short human title",
      "severity": "info|low|medium|high|critical",
      "summary": "what is happening",
      "evidence": ["specific evidence from logs"],
      "suspected_cause": "most likely cause or unknown",
      "recommended_fix": "concrete next action",
      "pr_recommended": true
    }
  ]
}
"""


@dataclass(frozen=True)
class Analyzer:
    settings: Settings

    async def analyze(
        self,
        incidents: list[Incident],
        target: Target | None = None,
        expected_logs: str | None = None,
    ) -> list[Finding]:
        if not incidents:
            return []
        return _run_codex(
            self.settings, self._prompt(incidents, target, expected_logs)
        )

    def _prompt(
        self,
        incidents: list[Incident],
        target: Target | None = None,
        expected_logs: str | None = None,
    ) -> str:
        blocks = "\n\n---\n\n".join(incident.to_prompt_block() for incident in incidents)
        remediation_mode = target.mode if target else self.settings.pr_mode
        repository = target.github_repo if target else str(self.settings.codex_workdir)
        expected_block = ""
        if expected_logs and expected_logs.strip():
            expected_block = (
                "The associated repository documents the following expected / benign "
                "log patterns. Treat incidents that clearly match them as normal "
                "operation and do not report them as findings.\n"
                "----- BEGIN EXPECTED LOGS -----\n"
                f"{expected_logs.strip()}\n"
                "----- END EXPECTED LOGS -----\n\n"
            )
        return (
            f"{AGENT_INSTRUCTIONS}\n\n"
            "Analyze these grouped Loki incidents from the configured application fleet.\n"
            "Find only issues that are actionable or worth watching.\n\n"
            f"Configured remediation mode: {remediation_mode}\n"
            f"Associated repository: {repository}\n\n"
            f"Operations runbook:\n{self._runbook_text()}\n\n"
            f"{expected_block}"
            f"{blocks}"
        )

    def _runbook_text(self) -> str:
        path = self.settings.runbook_path
        if path is None or not path.exists():
            return "No runbook configured."
        return path.read_text(encoding="utf-8")[:16_000]


def _run_codex(settings: Settings, prompt: str) -> list[Finding]:
    if not settings.codex_workdir.exists():
        raise RuntimeError(f"GUILTYSPARK_CODEX_WORKDIR does not exist: {settings.codex_workdir}")

    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".txt", delete=False) as output:
        output_path = Path(output.name)

    command = [
        settings.codex_path,
        "exec",
        "--cd",
        str(settings.codex_workdir),
        "--sandbox",
        "read-only" if settings.pr_mode in {"off", "plan"} else "workspace-write",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
    ]
    if settings.analysis_model_name:
        command.extend(["--model", settings.analysis_model_name])
    command.append("-")

    env = os.environ.copy()
    secret_markers = ("AUTH", "CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN", "WEBHOOK")
    for name in list(env):
        if name == settings.github_token_env or any(
            marker in name.upper() for marker in secret_markers
        ):
            env.pop(name, None)
    env["CODEX_HOME"] = str(settings.codex_home)

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=settings.codex_timeout_seconds,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"codex exec failed with exit {completed.returncode}: {details}")
        output_text = output_path.read_text(encoding="utf-8")
        payload = _extract_json(output_text)
        return [_finding_from_payload(item) for item in payload.get("findings", [])]
    finally:
        output_path.unlink(missing_ok=True)


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    if not stripped.startswith("{"):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1:
            raise RuntimeError(f"Codex returned non-JSON output: {text[:300]}")
        stripped = stripped[start : end + 1]
    return json.loads(stripped)


def _finding_from_payload(payload: dict[str, Any]) -> Finding:
    return Finding(
        fingerprint=str(payload.get("fingerprint", "unknown")),
        title=str(payload.get("title", "Untitled finding")),
        severity=str(payload.get("severity", "info")).lower(),
        summary=str(payload.get("summary", "")),
        evidence=[str(item) for item in payload.get("evidence", [])],
        suspected_cause=str(payload.get("suspected_cause", "unknown")),
        recommended_fix=str(payload.get("recommended_fix", "")),
        pr_recommended=bool(payload.get("pr_recommended", False)),
        raw=payload,
    )
