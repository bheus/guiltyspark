from __future__ import annotations

import argparse
import asyncio
import sys

from guiltyspark.config import Settings
from guiltyspark.loki import LokiClient
from guiltyspark.monitor import Monitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guiltyspark")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("once", help="poll Loki once and analyze incidents")
    subcommands.add_parser("daemon", help="run the monitor forever")
    subcommands.add_parser("doctor", help="check configuration and Loki reachability")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command == "doctor":
        return doctor(settings)
    if args.command == "once":
        summary = asyncio.run(Monitor(settings).run_once())
        print(
            f"events={summary.events} incidents={summary.incidents} "
            f"new_findings={summary.findings}"
        )
        return 0
    if args.command == "daemon":
        asyncio.run(Monitor(settings).run_forever())
        return 0
    return 2


def doctor(settings: Settings) -> int:
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

    try:
        now = 1_000_000_000
        LokiClient(
            settings.loki_url,
            bearer_token=settings.loki_bearer_token,
            basic_auth=settings.loki_basic_auth,
            timeout_seconds=5,
        ).query_range(settings.loki_query, 0, now, 1)
        print("loki_reachable=true")
    except Exception as exc:
        print(f"loki_reachable=false error={exc}")
        ok = False

    if not settings.codex_home.exists():
        print(f"codex_home_exists=false path={settings.codex_home}")
        ok = False
    if not settings.codex_workdir.exists():
        print(f"codex_workdir_exists=false path={settings.codex_workdir}")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
