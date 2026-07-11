from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VALID_MODES = {"observe", "fix", "draft-pr"}
TARGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
GITHUB_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Target:
    id: str
    loki_url: str
    loki_query: str
    github_repo: str
    base_branch: str = "main"
    mode: str = "observe"
    test_commands: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    max_changed_files: int = 12
    local_repo: Path | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Target":
        required = ("id", "loki_url", "loki_query", "github_repo")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            raise ValueError(f"target is missing required fields: {', '.join(missing)}")

        mode = str(payload.get("mode", "observe")).strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(
                f"target {payload['id']!r} mode must be one of: {', '.join(sorted(VALID_MODES))}"
            )

        max_changed_files = int(payload.get("max_changed_files", 12))
        if max_changed_files < 1:
            raise ValueError("max_changed_files must be at least 1")

        target_id = str(payload["id"]).strip()
        github_repo = str(payload["github_repo"]).strip().strip("/")
        if not TARGET_ID.fullmatch(target_id):
            raise ValueError(f"invalid target id: {target_id!r}")
        if not GITHUB_REPO.fullmatch(github_repo):
            raise ValueError(f"github_repo must use owner/repository form: {github_repo!r}")

        test_commands = tuple(str(item) for item in payload.get("test_commands", []))
        allowed_paths = tuple(str(item).strip("/") for item in payload.get("allowed_paths", []))
        if mode != "observe" and not test_commands:
            raise ValueError(f"target {target_id!r} requires test_commands in {mode} mode")
        if mode != "observe" and not allowed_paths:
            raise ValueError(f"target {target_id!r} requires allowed_paths in {mode} mode")

        local_repo_value = str(payload.get("local_repo", "")).strip()
        return cls(
            id=target_id,
            loki_url=str(payload["loki_url"]).rstrip("/"),
            loki_query=str(payload["loki_query"]),
            github_repo=github_repo,
            base_branch=str(payload.get("base_branch", "main")).strip(),
            mode=mode,
            test_commands=test_commands,
            allowed_paths=allowed_paths,
            max_changed_files=max_changed_files,
            local_repo=Path(local_repo_value).expanduser() if local_repo_value else None,
        )


def load_targets(path: Path) -> list[Target]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)

    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError(f"{path} must contain at least one [[targets]] entry")

    targets = [Target.from_dict(item) for item in raw_targets]
    ids = [target.id for target in targets]
    if len(ids) != len(set(ids)):
        raise ValueError("target ids must be unique")
    return targets
