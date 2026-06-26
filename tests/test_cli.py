import io
import os
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import URLError

from guiltyspark import cli
from guiltyspark.config import Settings


class CliTests(unittest.TestCase):
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
