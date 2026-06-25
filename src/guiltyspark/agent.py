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


AGENT_INSTRUCTIONS = """You are GuiltySpark, an observability agent for a homelab.

You inspect Loki incident summaries and find real bugs, misconfigurations, reliability
risks, security issues, and worthwhile improvements. You are skeptical of noisy logs:
do not invent causes. Tie each finding to evidence from the supplied incidents.

If PR mode is off, do not propose file edits. If PR mode is plan, produce a concrete
fix plan but do not change files. If PR mode is branch, you may suggest branch/PR work
only when the workspace is mounted and the evidence is strong.

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

    async def analyze(self, incidents: list[Incident]) -> list[Finding]:
        if not incidents:
            return []
        return _run_codex(self.settings, self._prompt(incidents))

    def _prompt(self, incidents: list[Incident]) -> str:
        blocks = "\n\n---\n\n".join(incident.to_prompt_block() for incident in incidents)
        return (
            f"{AGENT_INSTRUCTIONS}\n\n"
            "Analyze these grouped Loki incidents from my homelab/app fleet.\n"
            "Find only issues that are actionable or worth watching.\n\n"
            f"Configured PR mode: {self.settings.pr_mode}\n"
            f"Workspace: {self.settings.codex_workdir}\n\n"
            f"Homelab runbook:\n{self._runbook_text()}\n\n"
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
    if settings.model:
        command.extend(["--model", settings.model])
    command.append("-")

    env = os.environ.copy()
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
