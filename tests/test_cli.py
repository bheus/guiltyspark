import io
import os
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

from guiltyspark import cli
from guiltyspark.config import Settings
from guiltyspark.remediation import RemediationResult
from guiltyspark.targets import Target


class CliTests(unittest.TestCase):
    def test_replay_downgrades_pr_mode_without_allow_push(self) -> None:
        target = Target(
            id="app",
            loki_url="http://loki",
            loki_query="query",
            github_repo="owner/app",
            mode="pr",
            test_commands=("pytest",),
            allowed_paths=("src", "tests"),
        )
        args = Namespace(
            target="app",
            allow_push=False,
            patch_output=None,
            fixture=Path(__file__).parent / "fixtures" / "example-upstream-outage.json",
        )
        with TemporaryDirectory() as tmp:
            settings = Settings(
                loki_url="http://loki",
                loki_query="query",
                loki_limit=100,
                interval_seconds=60,
                lookback_seconds=60,
                state_path=Path(tmp) / "state.sqlite3",
                findings_path=Path(tmp) / "findings.jsonl",
                min_events=1,
                max_incidents_per_run=1,
                model=None,
                runbook_path=None,
                notify_webhook_url=None,
                codex_workdir=Path(tmp),
                codex_home=Path(tmp),
                codex_path="codex",
                codex_timeout_seconds=30,
                pr_mode="off",
            )
            with patch("guiltyspark.cli.Remediator") as remediator:
                remediator.return_value.repair.return_value = RemediationResult(
                    "validated", "tests passed"
                )
                self.assertEqual(cli.replay_incident(settings, [target], args), 0)

        replay_target = remediator.return_value.repair.call_args.args[0]
        self.assertEqual(replay_target.mode, "fix")

    def test_once_reports_loki_connection_error_without_traceback(self) -> None:
        with patch.dict("os.environ", {"LOKI_URL": "http://localhost:3100"}, clear=True):
            with patch("guiltyspark.cli.Monitor") as monitor_class:
                monitor_class.return_value.run_once.side_effect = URLError("connection refused")
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    exit_code = cli.main(["once"])

        self.assertEqual(exit_code, 1)
        self.assertIn("loki_error=connection refused", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_settings_load_dotenv_and_local_overrides(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "LOKI_URL=http://loki:3100\n"
                "LOKI_QUERY='{job=~\".+\"}'\n"
                "GUILTYSPARK_STATE_PATH=/data/state.sqlite3\n",
                encoding="utf-8",
            )
            (root / ".env.local").write_text(
                "GUILTYSPARK_STATE_PATH=data/local.sqlite3\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {}, clear=True):
                original_cwd = Path.cwd()
                try:
                    os.chdir(root)
                    settings = Settings.from_env()
                finally:
                    os.chdir(original_cwd)

        self.assertEqual(settings.loki_url, "http://loki:3100")
        self.assertEqual(settings.loki_query, '{job=~".+"}')
        self.assertEqual(settings.state_path, Path("data/local.sqlite3"))

    def test_doctor_queries_a_recent_loki_window(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                loki_url="http://loki:3100",
                loki_query="{job=~\".+\"}",
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
                codex_home=root,
                codex_path="codex",
                codex_timeout_seconds=30,
                pr_mode="off",
            )
            with patch("guiltyspark.cli.time.time_ns", return_value=100_000_000_000):
                with patch("guiltyspark.cli.LokiClient") as client:
                    self.assertEqual(cli.doctor(settings), 0)

            client.return_value.query_range.assert_called_once_with(
                settings.loki_query,
                99_000_000_000,
                100_000_000_000,
                1,
            )
