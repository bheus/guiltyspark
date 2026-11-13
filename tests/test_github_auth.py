import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from guiltyspark.github_auth import GitHubAuth
from guiltyspark.remediation import Remediator, load_replay_case
from test_remediation import settings, target


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

    def test_long_app_token_survives_cache_and_git_and_api_authorization(self) -> None:
        # Catch truncation or fixed-length validation of installation tokens.
        token = "ghs_" + "aB9_" * 42 + "." + "cD8_" * 43 + "." + "eF7_" * 43 + "Z0"
        self.assertEqual(len(token), 520)
        with tempfile.TemporaryDirectory() as tmp:
            app_settings = replace(
                settings(Path(tmp)),
                github_app_id="123",
                github_app_installation_id="456",
                github_app_private_key="private-key",
            )
            remediator = Remediator(app_settings)
            token_response = MagicMock()
            token_response.__enter__.return_value.read.return_value = json.dumps(
                {"token": token, "expires_at": "2099-01-01T00:00:00Z"}
            ).encode()
            pr_response = MagicMock()
            pr_response.__enter__.return_value.read.return_value = json.dumps(
                {"html_url": "https://github.com/owner/app/pull/1"}
            ).encode()
            _, finding = load_replay_case(
                Path(__file__).parent / "fixtures" / "example-upstream-outage.json"
            )
            with patch("guiltyspark.github_auth.jwt.encode", return_value="app-jwt"):
                with patch(
                    "guiltyspark.github_auth.urllib.request.urlopen",
                    side_effect=[token_response, pr_response],
                ) as urlopen:
                    self.assertEqual(remediator.github_auth.token(), token)
                    self.assertEqual(remediator.github_auth.token(), token)
                    self.assertEqual(urlopen.call_count, 1)
                    env = remediator._git_auth_env(required=True)
                    self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
                    self.assertEqual(
                        env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader"
                    )
                    prefix = "Authorization: Basic "
                    self.assertTrue(env["GIT_CONFIG_VALUE_0"].startswith(prefix))
                    credentials = base64.b64decode(
                        env["GIT_CONFIG_VALUE_0"][len(prefix):], validate=True
                    ).decode()
                    self.assertEqual(credentials, "x-access-token:" + token)
                    self.assertEqual(
                        remediator._create_pr(target(mode="draft-pr"), "fix/test", finding),
                        "https://github.com/owner/app/pull/1",
                    )
                    self.assertEqual(urlopen.call_count, 2)
                    request = urlopen.call_args.args[0]
                    self.assertEqual(
                        request.full_url, "https://api.github.com/repos/owner/app/pulls"
                    )
                    self.assertEqual(request.get_header("Authorization"), "Bearer " + token)

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
