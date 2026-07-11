# guiltyspark

`guiltyspark` is a deployable agent for watching Loki logs, spotting unknown problems, and turning the useful findings into actionable reports. It is designed to run on `apple-pi` or another always-on host.

The core loop is intentionally simple:

1. Query Loki for recent logs.
2. Group noisy log lines into incidents.
3. Ask `codex exec` to explain likely bugs, misconfigurations, and improvement opportunities.
4. Store findings locally so repeated noise does not alert forever.
5. Associate the incident with its configured GitHub repository.
6. In an isolated clone, let Codex prepare a minimal fix and regression tests.
7. Enforce patch policy, run validation, and optionally open a draft PR.

## What It Does

- Looks across any apps sending logs to Loki.
- Searches for errors, retries, auth failures, degraded services, suspicious restarts, slow requests, and repeated warnings.
- Summarizes evidence instead of forwarding raw log spam.
- Keeps SQLite state for cursors and duplicate suppression.
- Emits JSONL findings that can be tailed, shipped, or wired into notifications.
- Uses a ChatGPT-authenticated Codex session, so ChatGPT Pro can cover usage instead of a separately billed OpenAI API key.
- Runs on a Raspberry Pi with Docker Compose.

## What It Does Not Do Yet

- It never pushes code unless the target is explicitly configured in `draft-pr` mode.
- It does not assume this Codex chat controls the Pi.
- It does not require a local repo checkout unless you enable fix/PR workflows.

## Quick Start

```bash
cp .env.example .env
# Set GUILTYSPARK_TARGETS_JSON in .env or in Portainer.
docker compose pull
docker compose run --rm guiltyspark codex login --device-auth
docker compose up
```

To build the container locally instead of pulling GHCR:

```bash
docker build -t guiltyspark:local .
GUILTYSPARK_IMAGE=guiltyspark:local docker compose up
```

For a local dry run:

```bash
uv sync --dev --no-editable
cp .env.local.example .env.local
uv run --no-editable --reinstall-package guiltyspark guiltyspark once
```

Run tests locally with:

```bash
uv run --no-editable pytest
```

## Configuration

All settings are environment variables. The most important ones are:

| Variable | Purpose |
| --- | --- |
| `LOKI_URL` | Base URL for Loki, for example `http://loki:3100`. |
| `LOKI_QUERY` | LogQL query to monitor, for example `'{job=~".+"}'`. |
| `CODEX_HOME` | Persistent Codex auth/config directory. Defaults to `/data/codex` in Docker. |
| `GUILTYSPARK_INTERVAL_SECONDS` | Poll interval for daemon mode. |
| `GUILTYSPARK_LOOKBACK_SECONDS` | Initial lookback if no cursor exists. |
| `GUILTYSPARK_STATE_PATH` | SQLite state path. |
| `GUILTYSPARK_FINDINGS_PATH` | JSONL findings output path. |
| `GUILTYSPARK_RUNBOOK_PATH` | Markdown runbook the agent reads before analysis. |
| `GUILTYSPARK_NOTIFY_WEBHOOK_URL` | Optional generic JSON webhook for new findings. |
| `GUILTYSPARK_CODEX_PATH` | Codex CLI binary. Defaults to `codex`. |
| `GUILTYSPARK_CODEX_WORKDIR` | Local repo/config checkout Codex may inspect. |
| `GUILTYSPARK_PR_MODE` | `off`, `plan`, or `branch`. The scaffold defaults to `off`. |
| `GUILTYSPARK_TARGETS_PATH` | Optional TOML file mapping Loki queries to GitHub repositories. |
| `GUILTYSPARK_TARGETS_JSON` | JSON target list, intended for Portainer stack configuration. |
| `GUILTYSPARK_REMEDIATION_ROOT` | Parent directory for short-lived isolated clones. |
| `GUILTYSPARK_GITHUB_TOKEN_ENV` | Name of the environment variable containing the GitHub token. |

## Repository Targets

Fleet mode uses a TOML file containing one or more Loki-to-repository mappings. Start
from [`targets.example.toml`](targets.example.toml):

```toml
[[targets]]
id = "inventory-service"
loki_url = "http://loki:3100"
loki_query = '''{container=~"inventory-(api|worker)"}'''
github_repo = "example-org/inventory-service"
base_branch = "main"
mode = "observe"
test_commands = ["pytest -q"]
allowed_paths = ["src", "tests"]
max_changed_files = 8
```

Target modes are deliberately progressive:

- `observe`: detect and diagnose only.
- `fix`: clone, edit, enforce policy, and validate; never push.
- `draft-pr`: perform the same checks, then push a GuiltySpark branch and open a draft PR.

Production can supply the same structure as a JSON list through
`GUILTYSPARK_TARGETS_JSON`; this is the preferred Portainer configuration path and
takes precedence over the local TOML file. For private repositories and `draft-pr`
mode, provide a token through the environment variable named by
`GUILTYSPARK_GITHUB_TOKEN_ENV`. GuiltySpark injects it only into controller-owned Git
and GitHub requests; Codex does not receive it.

## Replaying A Captured Incident

Replay fixtures use the same generic incident and finding schema as the durable remediation
queue. For local replay, copy the target example to an ignored config, point `local_repo` at
the associated checkout, and use `fix` mode:

```bash
GUILTYSPARK_TARGETS_PATH=targets.local.toml guiltyspark replay \
  tests/fixtures/example-upstream-outage.json \
  --target inventory-service \
  --patch-output data/example.patch
```

Replay downgrades `draft-pr` to `fix` unless `--allow-push` is supplied. This makes the
same captured incident usable for local patch evaluation and an explicitly authorized
draft-PR exercise.

## apple-pi Deployment Shape

Push conventional commits to `main`. GitHub Actions tests the project, creates a semantic
release, builds a native `linux/arm64` image, publishes versioned and `latest` tags to GHCR,
then pins the released image on `main` for Portainer to poll. See
[`deploy/appl-pi.md`](deploy/appl-pi.md) for one-time setup and secrets.

## Commands

```bash
guiltyspark once      # poll Loki once and analyze current incidents
guiltyspark daemon    # run forever
guiltyspark doctor    # validate configuration and connectivity basics
```

## Notes On Auth

The monitor uses Codex CLI instead of `OPENAI_API_KEY`. Sign in once with your ChatGPT account inside the container:

```bash
docker compose run --rm guiltyspark codex login --device-auth
```

The login is stored under `CODEX_HOME`, which is mounted through `./data`.
