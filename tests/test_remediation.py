import tempfile
import unittest
import subprocess
from unittest.mock import patch
from pathlib import Path

from guiltyspark.config import Settings
from guiltyspark.agent import _run_codex
from guiltyspark.remediation import Remediator, load_replay_case
from guiltyspark.targets import Target


def settings(root: Path) -> Settings:
    return Settings(
        loki_url="http://loki",
        loki_query="query",
        loki_limit=100,
        interval_seconds=60,
        lookback_seconds=60,
        state_path=root / "state.sqlite3",
        findings_path=root / "findings.jsonl",
        min_events=1,
        max_incidents_per_run=1,
        model=None,
        runbook_path=None,
        notify_webhook_url=None,
        codex_workdir=root,
        codex_home=root / "codex",
        codex_path="codex",
        codex_timeout_seconds=30,
        pr_mode="off",
        remediation_root=root / "remediation",
    )


def target(**overrides) -> Target:
    values = {
        "id": "app",
        "loki_url": "http://loki",
        "loki_query": "query",
        "github_repo": "owner/app",
        "mode": "fix",
        "test_commands": ("true",),
        "allowed_paths": ("src", "tests"),
        "max_changed_files": 2,
    }
    values.update(overrides)
    return Target(**values)


class RemediationTests(unittest.TestCase):
    def test_example_replay_fixture_is_consistent(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "example-upstream-outage.json"
        incident, finding = load_replay_case(fixture)
        self.assertEqual(incident.fingerprint, finding.fingerprint)
        self.assertIn("NoneType", incident.samples[-1])
        self.assertTrue(finding.pr_recommended)

    def test_patch_policy_rejects_paths_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            with self.assertRaisesRegex(RuntimeError, "outside allowlist"):
                remediator._check_patch_policy(target(), ("src/app.py", ".env"))

    def test_patch_policy_rejects_too_many_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            with self.assertRaisesRegex(RuntimeError, "limit is 2"):
                remediator._check_patch_policy(
                    target(), ("src/a.py", "src/b.py", "tests/test_a.py")
                )

    def test_patch_policy_rejects_credential_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            with self.assertRaisesRegex(RuntimeError, "protected credential"):
                remediator._check_patch_policy(
                    target(allowed_paths=("config",)), ("config/service.pem",)
                )

    def test_validation_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            with self.assertRaisesRegex(RuntimeError, "no test_commands"):
                remediator._validate(Path(tmp), ())

    def test_validation_captures_successful_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            result = remediator._validate(Path(tmp), ("printf validated",))
            self.assertIn("validated", result)

    def test_validation_environment_does_not_receive_service_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            secrets = {
                "GITHUB_TOKEN": "github-secret",
                "LOKI_BEARER_TOKEN": "loki-secret",
                "OPENAI_API_KEY": "openai-secret",
            }
            with patch.dict("os.environ", secrets, clear=True):
                result = remediator._validate(Path(tmp), ("env",))
            for value in secrets.values():
                self.assertNotIn(value, result)

    def test_codex_environment_does_not_receive_github_token(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "example-upstream-outage.json"
        incident, finding = load_replay_case(fixture)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remediator = Remediator(settings(root))
            with patch.dict("os.environ", {"GITHUB_TOKEN": "secret"}, clear=True):
                with patch.object(remediator, "_run") as run:
                    remediator._run_codex(root, target(), incident, finding)

            codex_env = run.call_args.kwargs["env"]
            self.assertNotIn("GITHUB_TOKEN", codex_env)
            command = run.call_args.args[0]
            self.assertIn("--sandbox", command)
            self.assertIn("workspace-write", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_no_change_result_includes_codex_output(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "example-upstream-outage.json"
        incident, finding = load_replay_case(fixture)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remediator = Remediator(settings(root))
            with patch.object(remediator, "_clone"), patch.object(
                remediator, "_run_codex", return_value="sandbox setup failed"
            ), patch.object(
                remediator, "_changed_files", return_value=()
            ):
                result = remediator.repair(target(), incident, finding)

            self.assertEqual(result.status, "no-change")
            self.assertIn("sandbox setup failed", result.details)

    def test_diagnosis_environment_does_not_receive_service_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_settings = settings(root)

            def complete_codex(command, **kwargs):
                output_flag = command.index("--output-last-message")
                Path(command[output_flag + 1]).write_text(
                    '{"findings": []}', encoding="utf-8"
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            secrets = {
                "GITHUB_TOKEN": "github-secret",
                "LOKI_BEARER_TOKEN": "loki-secret",
                "OPENAI_API_KEY": "openai-secret",
                "GUILTYSPARK_NOTIFY_WEBHOOK_URL": "webhook-secret",
            }
            with patch.dict("os.environ", secrets, clear=True):
                with patch("subprocess.run", side_effect=complete_codex) as run:
                    self.assertEqual(_run_codex(app_settings, "prompt"), [])

            codex_env = run.call_args.kwargs["env"]
            for name in secrets:
                self.assertNotIn(name, codex_env)

    def test_pr_creation_uses_draft_and_redacts_secrets(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "example-upstream-outage.json"
        _, finding = load_replay_case(fixture)
        finding.evidence.append("Authorization: Bearer very-secret-token")
        with tempfile.TemporaryDirectory() as tmp:
            remediator = Remediator(settings(Path(tmp)))
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = (
                b'{"html_url":"https://github.com/owner/app/pull/1"}'
            )
            with patch.dict("os.environ", {"GITHUB_TOKEN": "github-secret"}, clear=True):
                with patch("urllib.request.urlopen", return_value=response) as urlopen:
                    pr_url = remediator._create_draft_pr(
                        target(),
                        "guiltyspark/app/incident-1",
                        finding,
                        "pytest passed; token=validation-secret",
                        ("src/app.py",),
                    )

            request = urlopen.call_args.args[0]
            body = request.data.decode("utf-8")
            self.assertEqual(pr_url, "https://github.com/owner/app/pull/1")
            self.assertIn('"draft": true', body)
            self.assertNotIn("very-secret-token", body)
            self.assertNotIn("validation-secret", body)
