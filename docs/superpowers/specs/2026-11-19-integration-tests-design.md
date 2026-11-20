# Integration and visual tests

## Why

Dependabot (added alongside this work) will open dependency bumps for the dashboard
(React, Vite, TypeScript), the Python service, and the site's nginx image. Today
nothing checks that a bump leaves the UI working. We want a suite that says, without a
manual look, whether an update changed how the dashboard or marketing site behaves or
looks. Same approach as the `website` repo's suite, with Abraham's fixture-server idea
for live data.

## Goals

- Run the real dashboard: built `frontend/` bundle served by `guiltyspark dashboard`
  (`dashboard.py`), backed by a seeded temp SQLite state and findings file and a fake
  Loki.
- Behavior tests and exact-pixel screenshots at desktop and phone sizes, in two data
  states: **busy** and **empty**.
- Every dashboard action round-trips through the real API.
- Marketing site (`site/`) served statically: screenshot plus link and file checks.
- Run automatically on Dependabot PRs; run locally before pushes that touch the UI.

## Non-goals

- Testing Codex, GitHub, or real Loki. Python unit tests already cover logic.
- Cross-browser coverage (Chromium only); Lighthouse.

## Anti-goals

- Flaky screenshots that train us to rerun or blindly update baselines.
- Tests that reach the network, spend Codex/GitHub quota, or need real credentials.
- Changing product behavior to make it testable, beyond an injectable setting where
  one is unavoidable (call it out in review).
- Slowing or gating the `main` release pipeline.

## Approaches

1. **Real fixture server** — the real `DashboardService` and HTTP handler over seeded
   state and a fake Loki. **Chosen**: exercises UI, API and storage together, so the
   JSON can't drift from what the server sends.
2. Mock `/api/*` in the browser from JSON files — simpler, but fixtures drift from the
   server and action tests only exercise mocks.

## Design

**Layout:** Playwright lives in `frontend/` (`@playwright/test` devDependency,
`playwright.config.js`, `e2e/`), as in Abraham. The fixture server is
`tests/e2e/fixture_server.py`, runnable as `uv run python -m tests.e2e.fixture_server`.

**Fixture server:** builds `Settings` pointing at a temp dir, seeds through the public
`StateStore` methods (`upsert_target`, `record_finding`, `record_target_finding`,
`record_remediation`, `record_issue_member`, `add_ignore_rule`, `set_ignored_note`,
...) and a findings JSONL, starts a fake Loki HTTP server returning fixed log events
for the anomaly queries, and serves the dashboard via `make_server`. One process per
state on its own port (busy 8099, empty 8098), each with a `/api/readiness` health URL.
Fixed timestamps relative to a frozen "now" so ages and sparklines are stable.

- **Codex:** `GUILTYSPARK_CODEX_PATH` points at a stub script that returns a fixed
  clustering result, so grouping is deterministic and never calls OpenAI.
- **GitHub:** no token; PR status must render its offline/unknown state without
  network. If `PrStatusClient` tries the network regardless, stub it at the
  `Settings` level, not in product code.
- Busy seed covers: open incidents across severities, a grouped anomaly cluster,
  unassigned anomalies, silenced items with a note and a rule, a remediation with an
  open PR, findings across several pages (pager), and more than one target.

**Container:** `mcr.microsoft.com/playwright:v<lockfile version>-noble`, linux/arm64,
plus pinned `uv` (checksum-verified, as in Abraham) and Python 3.12. Works on an
rsync'd copy excluding `.env`, `.git`, `node_modules`, `.venv`; `uv sync --locked`,
`npm ci` and `npm run build` in `frontend/`, then Playwright.

**Config:** as the website: desktop 1440×900 @1x and phone 390×844 @3x; exact pixels
(`maxDiffPixels: 0, threshold: 0`), `updateSnapshots: 'none'`, `retries: 0`,
`forbidOnly`; reduced motion, fixed locale/timezone, `page.clock.setFixedTime`; the
same deterministic Chromium raster flags; refuses to run outside the container.

**Fixture:** fails on console errors, page errors, and unexpected 4xx/5xx; awaits
fonts; settles polling (wait for the first round of `/api/*` responses before
asserting or capturing).

**Functional tests (busy unless noted):**
- every panel renders with seeded counts; empty state shows its empty copy;
- ignore one anomaly and a batch, add a note, add a rule (with suggest-pattern), edit
  rule metadata, and add/edit a target (protocol form) — each persists after reload;
- pager moves through findings;
- invalid pattern shows the server's validation error.

**Visual tests:** full dashboard, busy and empty, per project; marketing site full
page per project. Stale-baseline guard as in the website.

**Marketing site tests:** served by a static server in the container (or the
`site/nginx.conf` image if practical); links resolve to approved destinations;
`robots.txt`, `llms.txt`, `sitemap.xml`, favicons return 200.

**CI:** `.github/workflows/frontend-e2e.yml`, `pull_request` filtered to Dependabot,
`ubuntu-24.04-arm`, cache npm and uv, upload report on failure. `ci-release.yml`
unchanged.

**Pre-push hook:** `.githooks/pre-push` runs the suite when pushed commits touch
`frontend/`, `site/`, `tests/e2e/`, `src/guiltyspark/dashboard.py`, or the modules it
reads (`state.py`, `models.py`, `targets.py`, `clustering.py`, `config.py`,
`github_pr.py`); blocks on uncommitted changes in those paths.

**Docs:** a Testing section in the repo's agent/README docs.

**Dependabot:** ships with this work (already committed: Node 24 web stage and
`.github/dependabot.yml` with a `react` group).

## Left to the builder

- Exact seed contents and fake Loki event shapes, within the coverage list above.
- Whether the site is served by nginx or a Node static server.
- How polling is settled (response wait vs. clock control).
