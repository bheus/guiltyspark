"""Serve the real dashboard over seeded state, for the Playwright suite.

    uv run python -m tests.e2e.fixture_server --state busy --port 8099
"""

from __future__ import annotations

import argparse
import tempfile
import time
import urllib.request
from dataclasses import replace
from pathlib import Path

from tests.e2e import seeds


def _freeze_clock() -> None:
    # The dashboard measures windows and ages from the real clock; pin it so the
    # seeded events always fall in the same place. Monotonic time is untouched,
    # so socket timeouts and thread waits still behave.
    frozen = seeds.FROZEN_NOW.timestamp()
    time.time = lambda: frozen
    time.time_ns = lambda: seeds.FROZEN_NOW_NS
    import guiltyspark.dashboard as dashboard

    dashboard._now_iso = lambda: seeds.FROZEN_NOW.isoformat()  # noqa: SLF001


def _tag_loki_requests(state: str) -> None:
    opener = urllib.request.build_opener()
    opener.addheaders = [("X-E2E-State", state)]
    urllib.request.install_opener(opener)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", choices=("busy", "empty"), required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    _freeze_clock()
    _tag_loki_requests(args.state)

    from guiltyspark.config import Settings
    from guiltyspark.dashboard import make_server
    from guiltyspark.state import StateStore

    workdir = Path(tempfile.mkdtemp(prefix=f"guiltyspark-e2e-{args.state}-"))
    settings = replace(
        Settings.from_env(),
        loki_url=seeds.FAKES_URL,
        loki_query='{container=~".+"}',
        state_path=workdir / "state.sqlite3",
        findings_path=workdir / "findings.jsonl",
        codex_workdir=workdir,
        codex_home=workdir,
        codex_path=str(Path(__file__).with_name("fake_codex.py")),
        github_api_url=seeds.FAKES_URL,
        github_token_env="GUILTYSPARK_E2E_NO_TOKEN",
        github_app_id=None,
        github_app_installation_id=None,
        github_app_private_key=None,
        github_app_private_key_file=None,
        loki_bearer_token=None,
        loki_basic_auth=None,
        dashboard_grouping=True,
        targets_path=None,
        targets_json=None,
    )
    seeds.seed(args.state, StateStore(settings.state_path), settings.findings_path)
    server = make_server(settings, [], "127.0.0.1", args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
