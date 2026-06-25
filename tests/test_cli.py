import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch
from urllib.error import URLError

from guiltyspark import cli


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
