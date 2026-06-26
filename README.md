# guiltyspark

`guiltyspark` is a deployable agent for watching Loki logs, spotting unknown problems, and turning the useful findings into actionable reports. It is designed to run on `appl-pi` or another always-on homelab host.

The core loop is intentionally simple:

1. Query Loki for recent logs.
2. Group noisy log lines into incidents.
3. Ask `codex exec` to explain likely bugs, misconfigurations, and improvement opportunities.
4. Store findings locally so repeated noise does not alert forever.
5. Optionally let Codex inspect a configured repo/config checkout and prepare a fix plan.

## What It Does

- Looks across any apps sending logs to Loki.
- Searches for errors, retries, auth failures, degraded services, suspicious restarts, slow requests, and repeated warnings.
- Summarizes evidence instead of forwarding raw log spam.
- Keeps SQLite state for cursors and duplicate suppression.
- Emits JSONL findings that can be tailed, shipped, or wired into notifications.
- Uses a ChatGPT-authenticated Codex session, so ChatGPT Pro can cover usage instead of a separately billed OpenAI API key.
- Runs on a Raspberry Pi with Docker Compose.

## What It Does Not Do Yet

- It does not automatically push code without an explicit config gate.
- It does not assume this Codex chat controls the Pi.
- It does not require a local repo checkout unless you enable fix/PR workflows.

## Quick Start

```bash
cp .env.example .env
docker compose build
docker compose run --rm guiltyspark codex login --device-auth
docker compose up
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

## appl-pi Deployment Shape

Run this as a Compose service on `appl-pi` next to your Loki network or with `LOKI_URL` pointed at the reachable Loki endpoint. Mount `/data` for persistent state and findings.

If you want fix/PR workflows later, mount a read/write checkout under `/workspace` and set:

```env
GUILTYSPARK_CODEX_WORKDIR=/workspace
GUILTYSPARK_PR_MODE=plan
```

`plan` mode asks Codex for a fix plan and patch guidance. A later `branch` mode can prepare branches once repository credentials and safety rules are explicit.

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
