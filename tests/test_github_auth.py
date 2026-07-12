import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from guiltyspark.github_auth import GitHubAuth
from test_remediation import settings


class GitHubAuthTests(unittest.TestCase):
    def test_personal_token_remains_a_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auth = GitHubAuth(settings(Path(tmp)))
            with patch.dict("os.environ", {"GITHUB_TOKEN": "personal-token"}, clear=True):
                self.assertEqual(auth.token(), "personal-token")

    def test_partial_app_configuration_does_not_fall_back_to_personal_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_settings = replace(settings(Path(tmp)), github_app_id="123")
            auth = GitHubAuth(app_settings)
            with patch.dict("os.environ", {"GITHUB_TOKEN": "personal-token"}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "incomplete GitHub App"):
                    auth.token()

    def test_app_token_is_created_and_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app_settings = replace(
                settings(Path(tmp)),
                github_app_id="123",
                github_app_installation_id="456",
                github_app_private_key="-----BEGIN PRIVATE KEY-----\\nkey\\n-----END PRIVATE KEY-----",
            )
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(
                {
                    "token": "installation-token",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            ).encode()
            with patch("guiltyspark.github_auth.jwt.encode", return_value="app-jwt") as encode:
                with patch(
                    "guiltyspark.github_auth.urllib.request.urlopen",
                    return_value=response,
                ) as urlopen:
                    auth = GitHubAuth(app_settings)
                    self.assertEqual(auth.token(), "installation-token")
                    self.assertEqual(auth.token(), "installation-token")

            encode.assert_called_once()
            self.assertEqual(urlopen.call_count, 1)
            request = urlopen.call_args.args[0]
            self.assertEqual(
                request.full_url,
                "https://api.github.com/app/installations/456/access_tokens",
            )
            self.assertEqual(request.headers["Authorization"], "Bearer app-jwt")
            key = encode.call_args.args[1]
            self.assertIn("\nkey\n", key)

    def test_private_key_can_be_loaded_from_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "app.pem"
            key_path.write_text("private-key", encoding="utf-8")
            auth = GitHubAuth(
                replace(
                    settings(root),
                    github_app_id="123",
                    github_app_installation_id="456",
                    github_app_private_key_file=key_path,
                )
            )
            self.assertEqual(auth._private_key(), "private-key\n")
