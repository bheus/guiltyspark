# guiltyspark

`guiltyspark` is a deployable agent for watching Loki logs, spotting unknown problems, and turning the useful findings into actionable reports. It is designed to run on any always-on Docker host.

The core loop is intentionally simple:

1. Query Loki for recent logs.
2. Group noisy log lines into incidents.
3. Ask `codex exec` to explain likely bugs, misconfigurations, and improvement opportunities.
4. Store findings locally so repeated noise does not alert forever.
5. Associate the incident with its configured GitHub repository.
6. In an isolated clone, let Codex prepare a minimal fix and regression tests.
7. Enforce patch policy, run validation, and optionally open a PR.

## What It Does

- Looks across any apps sending logs to Loki.
- Searches for errors, retries, auth failures, degraded services, suspicious restarts, slow requests, and repeated warnings.
- Summarizes evidence instead of forwarding raw log spam.
- Keeps SQLite state for cursors and duplicate suppression.
- Emits JSONL findings that can be tailed, shipped, or wired into notifications.
- Uses a ChatGPT-authenticated Codex session, so ChatGPT Pro can cover usage instead of a separately billed OpenAI API key.
- Runs on a Raspberry Pi with Docker Compose.

## What It Does Not Do Yet

- It never pushes code unless the target is explicitly configured in `draft-pr` or `pr` mode.
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
| `RESEND_API_KEY` | Resend API key. Enables an email when guiltyspark opens a PR (only fires for its own PRs, never your manual ones). |
| `GUILTYSPARK_NOTIFY_EMAIL_FROM` | Verified Resend sender address for PR-opened emails. |
| `GUILTYSPARK_NOTIFY_EMAIL_TO` | Recipient address for PR-opened emails. |
| `GUILTYSPARK_CODEX_PATH` | Codex CLI binary. Defaults to `codex`. |
| `GUILTYSPARK_CODEX_WORKDIR` | Local repo/config checkout Codex may inspect. |
| `GUILTYSPARK_PR_MODE` | `off`, `plan`, or `branch`. The scaffold defaults to `off`. |
| `GUILTYSPARK_TARGETS_PATH` | Optional TOML file mapping Loki queries to GitHub repositories. |
| `GUILTYSPARK_TARGETS_JSON` | JSON target list, intended for Portainer stack configuration. |
| `GUILTYSPARK_REMEDIATION_ROOT` | Parent directory for short-lived isolated clones. |
| `GUILTYSPARK_GITHUB_TOKEN_ENV` | Name of the environment variable containing the GitHub token. |
| `GITHUB_APP_ID` | GitHub App ID. Takes precedence over personal-token authentication. |
| `GITHUB_APP_INSTALLATION_ID` | Installation ID for the account containing target repositories. |
| `GITHUB_APP_PRIVATE_KEY` | App private key as literal or `\n`-escaped PEM. |
| `GITHUB_APP_PRIVATE_KEY_FILE` | Alternative path to a mounted App private-key PEM. |

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
- `pr`: perform the same checks, then push a GuiltySpark branch and open a review-ready PR.

Production can supply the same structure as a JSON list through
`GUILTYSPARK_TARGETS_JSON`; this is the preferred Portainer configuration path and
takes precedence over the local TOML file. For private repositories and PR modes
mode, prefer a GitHub App installed only on the configured repositories. Set
`GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and either `GITHUB_APP_PRIVATE_KEY`
or `GITHUB_APP_PRIVATE_KEY_FILE`. GuiltySpark mints and caches short-lived
installation tokens for controller-owned Git and GitHub requests; Codex does not
receive App credentials or installation tokens. A token provided through
`GUILTYSPARK_GITHUB_TOKEN_ENV` remains available as a fallback when no App variables
are configured. Partial App configuration is an error.

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

Replay downgrades `draft-pr` and `pr` to `fix` unless `--allow-push` is supplied. This makes the
same captured incident usable for local patch evaluation and an explicitly authorized
draft-PR exercise.

## Deployment

Push conventional commits to `main`. GitHub Actions tests the project, creates a semantic
release, builds a native `linux/arm64` image, publishes versioned and `latest` tags to GHCR,
then pins the released image on `main` for a Git-based container orchestrator to poll.

Supply target mappings and credentials through environment variables at deployment time.
The repository intentionally contains no application-specific target profiles. Start each
target in `observe`, promote it to `fix` after reviewing diagnosis quality, and use
`draft-pr` or `pr` only after its validation commands and allowed paths are established.

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

The login is stored under `CODEX_HOME`, backed by the Compose-managed
`guiltyspark-data` volume.
