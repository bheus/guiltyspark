from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

import jwt

from guiltyspark.config import Settings


class GitHubAuth:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._installation_token: str | None = None
        self._installation_token_expires_at = 0.0

    def token(self, required: bool = True) -> str | None:
        app_values = (
            self.settings.github_app_id,
            self.settings.github_app_installation_id,
            self.settings.github_app_private_key,
            self.settings.github_app_private_key_file,
        )
        if any(app_values):
            self._validate_app_config()
            return self._app_installation_token()

        token = os.getenv(self.settings.github_token_env)
        if required and not token:
            raise RuntimeError(
                "GitHub authentication is required: configure a GitHub App or "
                f"set {self.settings.github_token_env}"
            )
        return token

    def _validate_app_config(self) -> None:
        missing: list[str] = []
        if not self.settings.github_app_id:
            missing.append("GITHUB_APP_ID")
        if not self.settings.github_app_installation_id:
            missing.append("GITHUB_APP_INSTALLATION_ID")
        if not (
            self.settings.github_app_private_key
            or self.settings.github_app_private_key_file
        ):
            missing.append("GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_FILE")
        if missing:
            raise RuntimeError(
                "incomplete GitHub App configuration; missing " + ", ".join(missing)
            )

    def _app_installation_token(self) -> str:
        if (
            self._installation_token
            and time.time() < self._installation_token_expires_at - 60
        ):
            return self._installation_token

        now = int(time.time())
        app_jwt = jwt.encode(
            {
                "iat": now - 60,
                "exp": now + 540,
                "iss": self.settings.github_app_id,
            },
            self._private_key(),
            algorithm="RS256",
        )
        request = urllib.request.Request(
            f"{self.settings.github_api_url}/app/installations/"
            f"{self.settings.github_app_installation_id}/access_tokens",
            data=b"{}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
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
            raise RuntimeError(
                f"GitHub App token creation failed with HTTP {exc.code}: {detail}"
            ) from exc

        token = str(payload.get("token", ""))
        if not token:
            raise RuntimeError("GitHub App token response did not include a token")
        self._installation_token = token
        expires_at = payload.get("expires_at")
        self._installation_token_expires_at = (
            datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp()
            if expires_at
            else time.time() + 3600
        )
        return token

    def _private_key(self) -> str:
        if self.settings.github_app_private_key_file:
            try:
                value = self.settings.github_app_private_key_file.read_text(
                    encoding="utf-8"
                )
            except OSError as exc:
                raise RuntimeError(
                    "could not read GITHUB_APP_PRIVATE_KEY_FILE: " + str(exc)
                ) from exc
        else:
            value = self.settings.github_app_private_key or ""
        return value.replace("\\n", "\n").strip() + "\n"
