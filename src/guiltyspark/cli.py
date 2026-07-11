from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from urllib.error import URLError

from guiltyspark.config import Settings
from guiltyspark.loki import LokiClient
from guiltyspark.monitor import FleetMonitor, Monitor, RunSummary
from guiltyspark.remediation import Remediator, load_replay_case
from guiltyspark.targets import Target, load_targets, load_targets_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guiltyspark")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("once", help="poll Loki once and analyze incidents")
    subcommands.add_parser("daemon", help="run the monitor forever")
    subcommands.add_parser("doctor", help="check configuration and Loki reachability")
    replay = subcommands.add_parser("replay", help="replay a saved incident against a target")
    replay.add_argument("fixture", type=Path, help="JSON replay fixture")
    replay.add_argument("--target", required=True, help="target id from the targets file")
    replay.add_argument(
        "--allow-push",
        action="store_true",
        help="honor draft-pr mode; without this flag replay never pushes",
    )
    replay.add_argument("--patch-output", type=Path, help="write the generated patch here")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if settings.targets_json:
        targets = load_targets_json(settings.targets_json)
    elif settings.targets_path:
        targets = load_targets(settings.targets_path)
    else:
        targets = []
    if args.command == "doctor":
        return doctor(settings, targets)
    if args.command == "replay":
        return replay_incident(settings, targets, args)
    if args.command == "once":
        try:
            if targets:
                summaries = asyncio.run(FleetMonitor(settings, targets).run_once())
            else:
                summaries = [asyncio.run(Monitor(settings).run_once())]
        except URLError as exc:
            print(
                f"loki_error={exc.reason} loki_url={settings.loki_url} "
                "hint='check LOKI_URL, .env.local, and Loki reachability'",
                file=sys.stderr,
            )
            return 1
        for summary in summaries:
            _print_summary(summary)
        return 0
    if args.command == "daemon":
        if targets:
            asyncio.run(FleetMonitor(settings, targets).run_forever())
        else:
            asyncio.run(Monitor(settings).run_forever())
        return 0
    return 2


def _print_summary(summary: RunSummary) -> None:
    print(
        f"target={summary.target_id} events={summary.events} incidents={summary.incidents} "
        f"new_findings={summary.findings} remediations={summary.remediations}"
    )


def replay_incident(settings: Settings, targets: list[Target], args: argparse.Namespace) -> int:
    matches = [target for target in targets if target.id == args.target]
    if not matches:
        print(f"unknown target={args.target!r}", file=sys.stderr)
        return 2
    target = matches[0]
    if target.mode == "observe":
        target = replace(target, mode="fix")
    elif target.mode == "draft-pr" and not args.allow_push:
        target = replace(target, mode="fix")

    incident, finding = load_replay_case(args.fixture)
    result = Remediator(settings).repair(target, incident, finding)
    if args.patch_output and result.patch:
        args.patch_output.parent.mkdir(parents=True, exist_ok=True)
        args.patch_output.write_text(result.patch, encoding="utf-8")
    print(
        f"target={target.id} replay={result.status} changed_files={len(result.changed_files)} "
        f"branch={result.branch or ''} pr_url={result.pr_url or ''}"
    )
    if result.details:
        print(result.details)
    return 0 if result.status in {"validated", "pr-opened"} else 1


def doctor(settings: Settings, targets: list[Target] | None = None) -> int:
    ok = True
    print(f"loki_url={settings.loki_url}")
    print(f"loki_query={settings.loki_query}")
    print(f"state_path={settings.state_path}")
    print(f"findings_path={settings.findings_path}")
    print(f"runbook_path={settings.runbook_path}")
    print(f"notify_webhook_configured={settings.notify_webhook_url is not None}")
    print(f"codex_home={settings.codex_home}")
    print(f"codex_path={settings.codex_path}")
    print(f"codex_workdir={settings.codex_workdir}")
    print(f"pr_mode={settings.pr_mode}")
    print(f"targets_path={settings.targets_path}")
    print(f"targets_from_env={settings.targets_json is not None}")
    print(f"targets={len(targets or [])}")

    checks = [
        (target.id, target.loki_url, target.loki_query) for target in (targets or [])
    ] or [("default", settings.loki_url, settings.loki_query)]
    for target_id, loki_url, loki_query in checks:
        try:
            now = 1_000_000_000
            LokiClient(
                loki_url,
                bearer_token=settings.loki_bearer_token,
                basic_auth=settings.loki_basic_auth,
                timeout_seconds=5,
            ).query_range(loki_query, 0, now, 1)
            print(f"target={target_id} loki_reachable=true")
        except Exception as exc:
            print(f"target={target_id} loki_reachable=false error={exc}")
            ok = False

    if not settings.codex_home.exists():
        print(f"codex_home_exists=false path={settings.codex_home}")
        ok = False
    if not settings.codex_workdir.exists():
        print(f"codex_workdir_exists=false path={settings.codex_workdir}")
        ok = False

    for target in targets or []:
        print(
            f"target={target.id} repo={target.github_repo} mode={target.mode} "
            f"tests={len(target.test_commands)}"
        )
        if target.local_repo is not None and not target.local_repo.exists():
            print(f"target={target.id} local_repo_exists=false path={target.local_repo}")
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
