from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from guiltyspark.config import Settings
from guiltyspark.github_auth import GitHubAuth
from guiltyspark.models import Finding, Incident
from guiltyspark.targets import Target


REPAIR_INSTRUCTIONS = """You are repairing a repository in response to a production incident.

Inspect the repository and the supplied log evidence, identify the code defect, and make the
smallest robust fix. Add or update regression tests. Do not commit, push, access credentials,
or create a pull request. Do not modify generated files or unrelated code. The controller will
review the diff and run validation after you finish.
"""

SECRET_VALUE = re.compile(
    r"(?i)(authorization|api[_-]?key|password|secret|token)(\s*[:=]\s*)([^\s,;]+)"
)
BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
CONVENTIONAL_TITLE = re.compile(
    r"(?i)^(?:build|chore|ci|docs|feat|fix|perf|refactor|style|test)"
    r"(?:\([^)]*\))?!?:\s*"
)


@dataclass(frozen=True)
class RemediationResult:
    status: str
    details: str
    patch: str = ""
    changed_files: tuple[str, ...] = ()
    branch: str | None = None
    pr_url: str | None = None


def load_replay_case(path: Path) -> tuple[Incident, Finding]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    incident_payload = payload["incident"]
    finding_payload = payload["finding"]
    incident = Incident(
        fingerprint=str(incident_payload["fingerprint"]),
        service=str(incident_payload["service"]),
        level=str(incident_payload["level"]),
        first_seen_ns=int(incident_payload["first_seen_ns"]),
        last_seen_ns=int(incident_payload["last_seen_ns"]),
        count=int(incident_payload["count"]),
        labels={str(k): str(v) for k, v in incident_payload.get("labels", {}).items()},
        samples=[str(item) for item in incident_payload.get("samples", [])],
    )
    finding = Finding(
        fingerprint=str(finding_payload["fingerprint"]),
        title=str(finding_payload["title"]),
        severity=str(finding_payload["severity"]),
        summary=str(finding_payload["summary"]),
        evidence=[str(item) for item in finding_payload.get("evidence", [])],
        suspected_cause=str(finding_payload["suspected_cause"]),
        recommended_fix=str(finding_payload["recommended_fix"]),
        pr_recommended=bool(finding_payload["pr_recommended"]),
        raw=finding_payload,
    )
    if incident.fingerprint != finding.fingerprint:
        raise ValueError("replay incident and finding fingerprints must match")
    return incident, finding


class Remediator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.github_auth = GitHubAuth(settings)

    def repair(self, target: Target, incident: Incident, finding: Finding) -> RemediationResult:
        if target.mode == "observe":
            return RemediationResult("skipped", "target mode is observe")

        self.settings.remediation_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"{target.id}-", dir=self.settings.remediation_root
        ) as temporary_dir:
            workspace = Path(temporary_dir) / "repo"
            try:
                self._clone(target, workspace)
                codex_output = self._run_codex(workspace, target, incident, finding)
                changed_files = self._changed_files(workspace)
                if not changed_files:
                    details = "Codex did not change any files"
                    if codex_output:
                        details += f":\n{self._redact(codex_output[-4000:])}"
                    return RemediationResult("no-change", details)
                self._check_patch_policy(target, changed_files)
                validation = self._validate(workspace, target.test_commands)
                changed_files = self._changed_files(workspace)
                self._check_patch_policy(target, changed_files)
                self._git(workspace, "add", "--intent-to-add", "--all")
                patch = self._git(workspace, "diff", "--binary", "--no-ext-diff").stdout

                if target.mode == "fix":
                    return RemediationResult(
                        "validated",
                        validation,
                        patch=patch,
                        changed_files=changed_files,
                    )

                branch = self._commit_and_push(workspace, target, finding)
                pr_url = self._create_draft_pr(target, branch, finding, validation, changed_files)
                return RemediationResult(
                    "pr-opened",
                    validation,
                    patch=patch,
                    changed_files=changed_files,
                    branch=branch,
                    pr_url=pr_url,
                )
            except Exception as exc:
                return RemediationResult("failed", str(exc))

    def _clone(self, target: Target, workspace: Path) -> None:
        source = (
            str(target.local_repo.resolve())
            if target.local_repo is not None
            else f"https://github.com/{target.github_repo}.git"
        )
        command = [
            "git",
            "clone",
            "--quiet",
            "--single-branch",
            "--branch",
            target.base_branch,
            source,
            str(workspace),
        ]
        self._run(command, env=self._git_auth_env(required=False))

    def _run_codex(
        self,
        workspace: Path,
        target: Target,
        incident: Incident,
        finding: Finding,
    ) -> str:
        prompt = (
            f"{REPAIR_INSTRUCTIONS}\n\n"
            f"Repository: {target.github_repo}\n"
            f"Base branch: {target.base_branch}\n\n"
            f"Diagnosis:\n{finding.summary}\n\n"
            f"Suspected cause:\n{finding.suspected_cause}\n\n"
            f"Recommended direction:\n{finding.recommended_fix}\n\n"
            f"Incident evidence:\n{incident.to_prompt_block()}\n"
        )
        command = [
            self.settings.codex_path,
            "exec",
            "--cd",
            str(workspace),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
        ]
        if self.settings.model:
            command.extend(["--model", self.settings.model])
        command.append("-")
        env = self._worker_env()
        env["CODEX_HOME"] = str(self.settings.codex_home.resolve())
        completed = self._run(
            command,
            input_text=prompt,
            env=env,
            timeout=self.settings.codex_timeout_seconds,
        )
        return (completed.stdout + completed.stderr).strip()

    def _changed_files(self, workspace: Path) -> tuple[str, ...]:
        output = self._git(workspace, "status", "--porcelain").stdout
        files: list[str] = []
        for line in output.splitlines():
            if not line:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            files.append(path)
        return tuple(sorted(files))

    def _check_patch_policy(self, target: Target, changed_files: tuple[str, ...]) -> None:
        if len(changed_files) > target.max_changed_files:
            raise RuntimeError(
                f"patch changes {len(changed_files)} files; limit is {target.max_changed_files}"
            )
        if not target.allowed_paths:
            raise RuntimeError("target has no allowed_paths; refusing an unrestricted patch")
        rejected = [
            path
            for path in changed_files
            if not any(path == prefix or path.startswith(f"{prefix}/") for prefix in target.allowed_paths)
        ]
        if rejected:
            raise RuntimeError(f"patch changes paths outside allowlist: {', '.join(rejected)}")
        protected = [
            path
            for path in changed_files
            if Path(path).name.startswith(".env")
            or Path(path).suffix.lower() in {".key", ".pem", ".p12", ".pfx"}
        ]
        if protected:
            raise RuntimeError(f"patch changes protected credential files: {', '.join(protected)}")

    def _validate(self, workspace: Path, commands: tuple[str, ...]) -> str:
        if not commands:
            raise RuntimeError("target has no test_commands; refusing to create an unvalidated PR")
        summaries: list[str] = []
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=workspace,
                shell=True,
                executable="/bin/sh",
                text=True,
                capture_output=True,
                env=self._worker_env(),
                timeout=self.settings.codex_timeout_seconds,
                check=False,
            )
            output = (completed.stdout + completed.stderr).strip()
            summaries.append(f"$ {command}\n{output[-4000:]}")
            if completed.returncode != 0:
                raise RuntimeError(
                    f"validation failed with exit {completed.returncode}:\n{summaries[-1]}"
                )
        return "\n\n".join(summaries)

    def _commit_and_push(self, workspace: Path, target: Target, finding: Finding) -> str:
        safe_fingerprint = re.sub(r"[^a-zA-Z0-9._-]", "-", finding.fingerprint)[:48]
        branch = f"guiltyspark/{target.id}/{safe_fingerprint}-{int(time.time())}"
        self._git(workspace, "switch", "-c", branch)
        self._git(workspace, "config", "user.name", "GuiltySpark")
        self._git(workspace, "config", "user.email", "guiltyspark[bot]@users.noreply.github.com")
        self._git(workspace, "add", "--all")
        self._git(workspace, "commit", "-m", f"fix: {finding.title}")
        self._git(workspace, "remote", "set-url", "origin", f"https://github.com/{target.github_repo}.git")
        self._run(
            ["git", "push", "--set-upstream", "origin", branch],
            cwd=workspace,
            env=self._git_auth_env(required=True),
        )
        return branch

    def _create_draft_pr(
        self,
        target: Target,
        branch: str,
        finding: Finding,
        validation: str,
        changed_files: tuple[str, ...],
    ) -> str:
        token = self._github_token(required=True)
        body = {
            "title": self._pr_title(finding),
            "head": branch,
            "base": target.base_branch,
            "draft": True,
            "body": self._pr_body(finding, validation, changed_files),
        }
        request = urllib.request.Request(
            f"{self.settings.github_api_url}/repos/{target.github_repo}/pulls",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub PR creation failed with HTTP {exc.code}: {detail}") from exc
        return str(payload["html_url"])

    def _pr_title(self, finding: Finding) -> str:
        subject = re.sub(r"\s+", " ", finding.title).strip()
        subject = re.sub(r"(?i)^\[guiltyspark\]\s*", "", subject)
        subject = CONVENTIONAL_TITLE.sub("", subject).strip()
        return f"fix: {subject or 'remediate production incident'}"

    def _pr_body(
        self, finding: Finding, validation: str, changed_files: tuple[str, ...]
    ) -> str:
        evidence = "\n".join(f"- {self._redact(item)}" for item in finding.evidence)
        files = "\n".join(f"- `{path}`" for path in changed_files)
        return (
            "## Incident\n\n"
            f"{self._redact(finding.summary)}\n\n"
            "## Evidence\n\n"
            f"{evidence}\n\n"
            "## Suspected Cause\n\n"
            f"{self._redact(finding.suspected_cause)}\n\n"
            "## Changed Files\n\n"
            f"{files}\n\n"
            "## Validation\n\n"
            f"```text\n{self._redact(validation[-6000:])}\n```\n\n"
            f"Incident fingerprint: `{finding.fingerprint}`\n"
        )

    def _redact(self, value: str) -> str:
        redacted = BEARER_VALUE.sub("Bearer [REDACTED]", value)
        return SECRET_VALUE.sub(r"\1\2[REDACTED]", redacted)

    def _github_token(self, required: bool) -> str | None:
        return self.github_auth.token(required)

    def _worker_env(self) -> dict[str, str]:
        env = os.environ.copy()
        secret_markers = (
            "AUTH",
            "CREDENTIAL",
            "KEY",
            "PASSWORD",
            "SECRET",
            "TOKEN",
            "WEBHOOK",
        )
        for name in list(env):
            if name == self.settings.github_token_env or any(
                marker in name.upper() for marker in secret_markers
            ):
                env.pop(name, None)
        env.pop("CODEX_HOME", None)
        return env

    def _git_auth_env(self, required: bool) -> dict[str, str]:
        env = self._worker_env()
        token = self._github_token(required)
        if token:
            credentials = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credentials}",
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
        return env

    def _git(self, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self._run(["git", *args], cwd=workspace)

    def _run(
        self,
        command: list[str],
        cwd: Path | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            details = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(f"{' '.join(command[:2])} failed with exit {completed.returncode}: {details}")
        return completed
