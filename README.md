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

### Dashboard frontend

The dashboard UI is a React + TypeScript app in `frontend/` (Vite). Its build
output lands in `src/guiltyspark/web/`, which the Python backend serves via
`importlib.resources`. That directory is **git-ignored build output** — the Docker
image builds it in a Node stage, and the wheel force-includes it.

```bash
# Day-to-day UI work: Vite dev server (proxies /api to a running dashboard on :8343)
guiltyspark dashboard &            # or run the daemon/compose service
npm --prefix frontend install
npm --prefix frontend run dev      # open the printed http://localhost:5173

# Produce the bundle the Python server serves directly
npm --prefix frontend run build
```

Because `src/guiltyspark/web/` is not committed, a bare `guiltyspark dashboard`
from a fresh checkout serves nothing until you run the build once (or use the dev
server). Docker builds handle this automatically.

## Configuration

All settings are environment variables. The most important ones are:

| Variable | Purpose |
| --- | --- |
| `LOKI_URL` | Base URL for Loki, for example `http://loki:3100`. |
| `LOKI_QUERY` | LogQL query to monitor, for example `'{job=~".+"}'`. |
| `LOKI_LIMIT` | Maximum log lines fetched per query range. Defaults to `5000`. |
| `LOKI_BEARER_TOKEN` | Optional bearer token for an authenticated Loki instance. |
| `LOKI_BASIC_AUTH` | Optional `user:password` basic-auth credential for Loki. |
| `CODEX_HOME` | Persistent Codex auth/config directory. Defaults to `/data/codex` in Docker. |
| `GUILTYSPARK_INTERVAL_SECONDS` | Poll interval for daemon mode. Defaults to `300`. |
| `GUILTYSPARK_LOOKBACK_SECONDS` | Initial lookback if no cursor exists. Defaults to `900`. |
| `GUILTYSPARK_MIN_EVENTS` | Minimum events before a group of log lines counts as an incident. Defaults to `2`. |
| `GUILTYSPARK_MAX_INCIDENTS_PER_RUN` | Cap on incidents analyzed in a single poll cycle. Defaults to `8`. |
| `GUILTYSPARK_MAX_REMEDIATIONS_PER_RUN` | Cap on queued corrective measures attempted per target in one cycle. This throttles released observation backlogs and defaults to `3`. |
| `GUILTYSPARK_STATE_PATH` | SQLite state path. |
| `GUILTYSPARK_FINDINGS_PATH` | JSONL findings output path. |
| `GUILTYSPARK_RUNBOOK_PATH` | Markdown runbook the agent reads before analysis. |
| `GUILTYSPARK_NOTIFY_WEBHOOK_URL` | Optional generic JSON webhook for new findings. |
| `RESEND_API_KEY` | Resend API key. Enables an email when guiltyspark opens a PR (only fires for its own PRs, never your manual ones). |
| `GUILTYSPARK_NOTIFY_EMAIL_FROM` | Verified Resend sender address for PR-opened emails. |
| `GUILTYSPARK_NOTIFY_EMAIL_TO` | Recipient address for PR-opened emails. |
| `GUILTYSPARK_MODEL` | Backward-compatible default model for every `codex exec` call. The use-specific settings below take precedence. Unset uses the Codex CLI default. |
| `GUILTYSPARK_ANALYSIS_MODEL` | Model for diagnosis, anomaly clustering, issue deduplication, and planning. Falls back to `GUILTYSPARK_MODEL`, then the Codex CLI default. |
| `GUILTYSPARK_REMEDIATION_MODEL` | Model for repository-writing code fixes. Falls back to `GUILTYSPARK_MODEL`, then the Codex CLI default. The Docker Compose scaffold defaults this to `gpt-5.6-luna`. |
| `GUILTYSPARK_CODEX_PATH` | Codex CLI binary. Defaults to `codex`. |
| `GUILTYSPARK_CODEX_WORKDIR` | Local repo/config checkout Codex may inspect. |
| `GUILTYSPARK_CODEX_TIMEOUT_SECONDS` | Timeout for a single Codex invocation. Defaults to `600`. |
| `GUILTYSPARK_PR_MODE` | `off`, `plan`, or `branch`. The scaffold defaults to `off`. |
| `GUILTYSPARK_TARGETS_PATH` | Optional TOML file mapping Loki queries to GitHub repositories. Seeds the target store on first run only; the DB is authoritative thereafter. |
| `GUILTYSPARK_TARGETS_JSON` | JSON target list, intended for Portainer stack configuration. Seeds the target store on first run only; edit targets from the dashboard afterward. |
| `GUILTYSPARK_REMEDIATION_ROOT` | Parent directory for short-lived isolated clones. |
| `GUILTYSPARK_GITHUB_TOKEN_ENV` | Name of the environment variable containing the GitHub token. |
| `GUILTYSPARK_GITHUB_API_URL` | GitHub API base URL, for GitHub Enterprise. Defaults to `https://api.github.com`. |
| `GUILTYSPARK_DASHBOARD_HOST` | Bind address for `guiltyspark dashboard`. Defaults to `0.0.0.0`. |
| `GUILTYSPARK_DASHBOARD_PORT` | Port for the web dashboard. Defaults to `8343`. |
| `GUILTYSPARK_SITE_IMAGE` | Marketing-site image. Defaults to `ghcr.io/bheus/guiltyspark-site:latest`. |
| `GUILTYSPARK_SITE_PORT` | Host port for the nginx marketing-site container. Defaults to `8080`. |
| `GUILTYSPARK_DASHBOARD_GROUPING` | When enabled, the dashboard asks Codex to cluster related unassigned anomalies into a single semantic group so an operator can silence a whole class at once, and to propose a **silence pattern** (a service-scoped regex) that suppresses current *and future* variants. Patterns are always operator-reviewed before they take effect — the UI shows the proposal and its live blast radius; nothing is auto-applied. Costs a Codex call when a new anomaly class appears (clustering is cached against the unassigned fingerprint set; count-only changes reuse it) and one per pattern proposal. Requires the `codex` binary. Falls back to the flat list on any Codex error. Defaults to `false`. |
| `GUILTYSPARK_DASHBOARD_FILTER_LABEL` | Loki label the dashboard's container selector filters on. The picker lists this label's values for the current window and narrows the anomaly survey server-side, so the `LOKI_LIMIT` budget is spent only on the selected containers. Defaults to `container`. |
| `GUILTYSPARK_DASHBOARD_MAX_EVENTS` | Safety ceiling on how many lines the dashboard's anomaly survey fetches per severity query. The survey filters severity in Loki and pages through the window `LOKI_LIMIT` lines at a time, so it fetches roughly the anomaly count rather than the window's whole volume — a 24h window of ~550k lines costs ~2k fetched. Reaching this cap is unusual and surfaces as a truncation notice. Defaults to `25000`. |
| `GUILTYSPARK_DEDUP_ISSUES` | When enabled (default), remediation dedups on the *logical issue* rather than the exact fingerprint: Codex clusters near-duplicate anomalies into one persistent issue, only one representative is remediated per issue per run, and an issue whose PR is still open — or whose last PR merged/closed within the cooldown — is not re-filed. Costs a Codex call only when a genuinely new fingerprint appears (known fingerprints short-circuit from the store); falls back to per-fingerprint dedup on any Codex error. Requires the `codex` binary. Defaults to `true`. |
| `GUILTYSPARK_ISSUE_COOLDOWN_SECONDS` | How long after an issue's last PR (merged or closed) to suppress re-filing it. Open PRs always suppress regardless of this value. Defaults to `604800` (7 days). |
| `GUILTYSPARK_ISSUE_ACTIVE_WINDOW_SECONDS` | How far back known issues are considered when matching a new anomaly to an existing issue (bounds the clustering prompt). Defaults to `1209600` (14 days). |
| `GUILTYSPARK_EXPECTED_LOGS_CACHE_SECONDS` | How long to cache each target's fetched `expected_logs_path` document before re-fetching it from GitHub. Defaults to `300` (5 minutes). |
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
expected_logs_path = "docs/EXPECTED_LOGS.md"
```

`expected_logs_path` (optional) is a repo-relative path to a document listing log
lines the service emits that are *expected* or benign — deliberate warnings, startup
chatter, retry notices. The Monitor fetches it from the target repository (private
repos included, via the same GitHub auth) and hands it to Codex as context, so those
patterns are not mistaken for anomalies. A missing or unreachable file is simply
ignored — analysis proceeds without the extra context. The fetch is cached per
`GUILTYSPARK_EXPECTED_LOGS_CACHE_SECONDS`.

Target modes are deliberately progressive:

- `observe`: detect and diagnose only; retain PR-recommended anomalies as held remediation dossiers. When promoting the protocol in the dashboard, the operator may explicitly release that backlog for corrective action.
- `fix`: clone, edit, enforce policy, and validate; never push.
- `draft-pr`: perform the same checks, then push a GuiltySpark branch and open a draft PR.
- `pr`: perform the same checks, then push a GuiltySpark branch and open a review-ready PR.

Production can supply the same structure as a JSON list through
`GUILTYSPARK_TARGETS_JSON`; this is the preferred Portainer configuration path and
takes precedence over the local TOML file.

Targets are stored in the SQLite state database and are editable from the dashboard
(see below). `GUILTYSPARK_TARGETS_JSON` / `GUILTYSPARK_TARGETS_PATH` **seed** that
store once, on first run while it holds no targets; after that the database is
authoritative and dashboard edits persist across restarts. Changing the env var later
has no effect unless the store is empty, and deleting a seeded target from the
dashboard is not undone by a restart. The daemon re-reads targets from the store at
the start of every poll cycle, so edits take effect within one interval without a
restart.

For private repositories and PR modes
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

The Compose stack also serves the static marketing site through an nginx sidecar at
`http://localhost:8080`. Set `GUILTYSPARK_SITE_PORT` to publish it on a different
host port. The dashboard remains available separately on port `8343`. Main-branch
pushes publish `site/` as `ghcr.io/bheus/guiltyspark-site:latest` before triggering
the Portainer redeploy webhook, so the stack does not depend on repository bind mounts.

Push conventional commits to `main`. GitHub Actions tests the project, creates a semantic
release, builds a native `linux/arm64` image, publishes versioned and `latest` tags to GHCR,
then triggers the GuiltySpark Portainer stack webhook so the stack re-pulls `latest`.

For the one-time Portainer setup, create the stack from this repository's `main` branch,
enable the stack webhook in Portainer, and save its generated URL as the GitHub repository
secret `PORTAINER_GUILTYSPARK_WEBHOOK`. The release workflow calls that webhook only after
the new image has been pushed. If the secret is absent, the webhook step is skipped and
Portainer's normal Git polling remains the fallback.

Supply target mappings and credentials through environment variables at deployment time.
The repository intentionally contains no application-specific target profiles. Start each
target in `observe`, promote it to `fix` after reviewing diagnosis quality, and use
`draft-pr` or `pr` only after its validation commands and allowed paths are established.

## Commands

```bash
guiltyspark once      # poll Loki once and analyze current incidents
guiltyspark daemon    # run forever
guiltyspark doctor    # validate configuration and connectivity basics
guiltyspark dashboard # serve the web dashboard (default port 8343)
```

## Dashboard

`guiltyspark dashboard` serves a Monitor-voiced web console (default
`http://localhost:8343`). In Docker it runs as the `guiltyspark-dashboard` compose
service, which shares the daemon's `/data` volume and publishes port 8343. It shows
catalogued findings, remediation history, and a live
Loki view of recent error-severity events. Each live incident is classified into the
target whose stream selector matches its labels; anything that matches no configured
target is surfaced as an **unassigned anomaly**, so errors outside your containment
protocols are still visible. A container picker (backed by
`GUILTYSPARK_DASHBOARD_FILTER_LABEL`) narrows the survey server-side, so the
`LOKI_LIMIT` budget is spent only on the containers you selected.

The dashboard is also the control surface for configuration:

- **Containment protocols** — add, amend, or decommission targets. Edits are validated
  with the same rules as the config file, written to the state store, and picked up by
  the daemon on its next cycle.
- **Silence noise** — an unassigned anomaly you judge to be noise can be silenced; it is
  suppressed from the stream (keyed by its incident fingerprint) and listed under
  **Silenced anomalies**. Silencing captures the anomaly's service, level, a sample line,
  and event count so the entry stays legible after it leaves the stream, and each entry
  carries an editable triage note for your own reference. Any entry can be restored.
- **Silence patterns** — a durable, service-scoped regex rule that suppresses current
  *and future* variants of a noise class, rather than one fingerprint at a time. With
  `GUILTYSPARK_DASHBOARD_GROUPING` enabled, Codex proposes the pattern and the UI shows
  its live blast radius; nothing is applied until you commit it. Rules carry an editable
  title and note, and an invalid regex is skipped rather than breaking the anomaly view.

With `GUILTYSPARK_DASHBOARD_GROUPING` enabled, clustering runs **off the request path**:
a worker computes groups in the background, the API returns `groups_pending` while that
is in flight, and the UI shows a cataloging state over the flat fallback list, polling
every 2s until the groups land.

The page is a React + TypeScript app (in `frontend/`, see Development above) that
talks only to the JSON API, so backend and client evolve independently. It polls
every 60s and reconciles in place — open incident cards and in-progress edits
survive a refresh. Read endpoints: `/api/overview`, `/api/findings`,
`/api/remediations`, `/api/anomalies?minutes=N[&containers=…]`,
`/api/containers?minutes=N`, `/api/targets`, `/api/anomalies/ignored` (silenced
fingerprints and pattern rules). Write endpoints: `POST`/`DELETE /api/targets`,
`POST`/`DELETE /api/anomalies/ignore`, `POST /api/anomalies/ignore-batch` (silence a
whole group), `POST /api/anomalies/suggest-pattern` (ask Codex for a pattern
proposal), `POST`/`DELETE /api/anomalies/rules`, `POST /api/anomalies/rules/metadata`
(edit a rule's title/note), and `POST /api/anomalies/note` (edit a silenced anomaly's
triage note).

The dashboard is **unauthenticated**, and it can now modify configuration and trigger
target changes. Keep it on a trusted LAN and do not expose it to the public internet.

## Notes On Auth

The monitor uses Codex CLI instead of `OPENAI_API_KEY`. Sign in once with your ChatGPT account inside the container:

```bash
docker compose run --rm guiltyspark codex login --device-auth
```

The login is stored under `CODEX_HOME`, backed by the Compose-managed
`guiltyspark-data` volume.
