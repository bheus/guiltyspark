# GuiltySpark Integration and Visual Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Playwright suite (behavior + exact-pixel screenshots) over the real dashboard with seeded data, plus the marketing site, so Dependabot bumps are judged automatically.

**Architecture:** `tests/e2e/fixture_server.py` runs the real `make_server` dashboard over seeded temp state with fake Loki, fake GitHub API and a stub Codex. Playwright in `frontend/` drives it inside a pinned linux/arm64 container. Reference implementations: `~/Documents/GitHub/website/e2e` (container, fixture, guard, hook) and `~/Documents/GitHub/abraham/frontend/e2e` (uv in container, Python fixture server).

**Tech Stack:** `@playwright/test`, Python 3.12 + uv, Node 24 build, Docker, GitHub Actions `ubuntu-24.04-arm`.

**Spec:** `docs/superpowers/specs/2026-11-19-integration-tests-design.md`

## Global Constraints

- No network from tests: Loki → fake on `127.0.0.1:8097`; `github_api_url` → same fake; `codex_path` → `tests/e2e/fake_codex.sh`. No real tokens; `.env` never copied into the container.
- No product-code changes unless unavoidable; any such change is a ledger ruling and called out in the PR.
- Ports: busy dashboard `8099`, empty dashboard `8098`, fakes `8097`, marketing site `8096`.
- Frozen now: `2026-06-15T12:00:00Z`, used by seeds (timestamps relative to it) and `page.clock.setFixedTime`.
- Playwright config values identical to `website/playwright.config.js` (projects, exact pixels, `updateSnapshots: 'none'`, `retries: 0`, `forbidOnly`, reduced motion, raster flags, refuses to run unless `E2E_CONTAINER=1`).
- `ci-release.yml` is not modified.
- Comments only for non-obvious nuance (`~/.claude/CLAUDE.md`).

## Review Focus

1. Polling (`usePolling`) refetches mid-capture → fixture waits for the first `/api/overview` and `/api/anomalies` responses and the clock is fixed; screenshot runs 8× clean (Task 4).
2. Clustering runs async in a worker thread; screenshot taken before groups land → busy fixture waits for `/api/anomalies` to report the grouped cluster before capturing (Task 3 test `grouped cluster renders`).
3. Action tests mutate the shared busy server and break other tests' counts → mutating tests use their own target/fingerprints, and count assertions only read untouched seeds (Task 3).
4. Fake Loki ignores the query's time window and returns events outside lookback → seeds stamp events within lookback of frozen now; test `every panel renders` asserts exact counts (Task 3).
5. PR status hits real GitHub when `github_api_url` is ignored somewhere → fake records any unexpected path, and the fixture fails if the fake saw a request it did not serve (Task 2).

---

### Task 1: Container runner, config, Dependabot already present

**Files:**
- Create: `frontend/e2e/Dockerfile`, `frontend/e2e/run.sh`, `frontend/e2e/in-container.sh`, `frontend/playwright.config.js`, `frontend/e2e/smoke.spec.js` (deleted in Task 3)
- Modify: `frontend/package.json` (`@playwright/test`, scripts `test:e2e`, `test:visual:update`, `test:e2e:report`), `.gitignore`, `.dockerignore`

**Interfaces:**
- Produces: `npm --prefix frontend run test:e2e [-- args]`; env in container `E2E_CONTAINER=1`.

- [ ] **Step 1:** Dockerfile: Playwright image + `rsync` + uv 0.11.7 with Abraham's checksum + `uv python install 3.12`, `NPM_CONFIG_UPDATE_NOTIFIER=false`.
- [ ] **Step 2:** `run.sh`/`in-container.sh` modeled on Abraham's; in-container: `uv sync --locked`, `(cd frontend && npm ci && npm run build)`, then `npx playwright test`.
- [ ] **Step 3:** Config with Global Constraints; one `webServer` for now: `python3 -m http.server 8096 -d ../site` with url `/robots.txt`.
- [ ] **Step 4:** Smoke spec: `GET http://127.0.0.1:8096/` is 200. Run `npm --prefix frontend run test:e2e` → 2 passed; host `npx playwright test` refuses.
- [ ] **Step 5:** Commit `test: add containerized Playwright runner`.

### Task 2: Fixture server, fakes, and shared fixture

**Files:**
- Create: `tests/e2e/__init__.py`, `tests/e2e/fixture_server.py`, `tests/e2e/seeds.py`, `tests/e2e/fakes.py`, `tests/e2e/fake_codex.sh`, `tests/test_e2e_fixture.py`, `frontend/e2e/fixtures.js`
- Modify: `frontend/playwright.config.js`

**Interfaces:**
- `python -m tests.e2e.fixture_server --state {busy,empty} --port N` — builds `Settings` in a temp dir (`dashboard_grouping=True`, `loki_url` and `github_api_url` → `http://127.0.0.1:8097`, `codex_path` → stub), seeds, `make_server(...).serve_forever()`.
- `python -m tests.e2e.fakes --port 8097` — serves `GET /loki/api/v1/query_range` (events from `seeds.loki_events()`), GitHub `GET /repos/{owner}/{repo}/pulls/{n}` (fixed open PR), `GET /__unexpected` (JSON list of unserved paths), `GET /__health`.
- `seeds.py`: `FROZEN_NOW: datetime`, `seed_busy(state: StateStore, findings_path: Path) -> None`, `seed_empty(...)`, `loki_events() -> list[dict]`, `targets() -> list[Target]`.
- `fake_codex.sh` prints the fixed clustering JSON `clustering.py` expects (read its parser to match).
- `fixtures.js`: same contract as `website/e2e/fixtures.js` (`test`, `expect`, `allowFailures`, `expectBaseline`, `gotoSettled`), plus `gotoSettled` waits for `/api/overview` and `/api/anomalies` 200s and fonts, and an afterEach that fails if `GET 127.0.0.1:8097/__unexpected` is non-empty.

- [ ] **Step 1:** `tests/test_e2e_fixture.py` (pytest): `seed_busy` into a tmp `StateStore` then `DashboardService(settings).overview()["counts"]` equals the expected counts dict you define in `seeds.py` as `BUSY_COUNTS`; empty seed gives all-zero counts.
- [ ] **Step 2:** Run `uv run pytest tests/test_e2e_fixture.py` → FAIL (module missing).
- [ ] **Step 3:** Implement seeds, fakes, stub, server. Busy coverage per spec: ≥2 targets, incidents across all severities, a Codex-grouped cluster, unassigned anomalies, silenced item with note and a rule, a remediation with an open PR, findings > one page.
- [ ] **Step 4:** pytest → PASS. Add webServers: fakes (`/__health`), busy (`:8099/api/readiness`), empty (`:8098/api/readiness`), site.
- [ ] **Step 5:** Commit `test: add dashboard fixture server and fakes`.

### Task 3: Functional tests

**Files:**
- Create: `frontend/e2e/functional/dashboard.spec.js`, `frontend/e2e/functional/actions.spec.js`, `frontend/e2e/functional/empty.spec.js`, `frontend/e2e/functional/site.spec.js`
- Delete: `frontend/e2e/smoke.spec.js`

These cover existing behavior: prove each fails by a temporary mutation (seed count, copy, link) before committing.

- [ ] **Step 1:** `dashboard.spec.js` (busy): `every panel renders` (tiles show `BUSY_COUNTS`), `grouped cluster renders`, `pager moves through findings`.
- [ ] **Step 2:** `actions.spec.js` (busy, each on its own seeded fingerprint/target): ignore one, ignore batch, add note, suggest-pattern then add rule, edit rule metadata, add target and edit target via protocol form — each visible after `page.reload()`; `invalid pattern shows server error` (`allowFailures` 400).
- [ ] **Step 3:** `empty.spec.js` (`baseURL :8098`): each panel's empty copy.
- [ ] **Step 4:** `site.spec.js` (`baseURL :8096`): headline present; outbound links match the hrefs in `site/index.html` (list them explicitly); `robots.txt`, `llms.txt`, `sitemap.xml`, `favicon.ico`, `favicon.svg`, `apple-touch-icon.png` → 200.
- [ ] **Step 5:** Full run green; mutations caught. Commit `test: add dashboard and site functional coverage`.

### Task 4: Visual baselines and guard

**Files:**
- Create: `frontend/e2e/visual/pages.spec.js`, `frontend/e2e/global-teardown.js` (port from website), `frontend/e2e/__screenshots__/**`

- [ ] **Step 1:** Shots: `dashboard-busy.png`, `dashboard-empty.png`, `site.png`, all `fullPage`.
- [ ] **Step 2:** Run → fails on missing baselines. `test:visual:update`; inspect each PNG by eye (no "loading", real data, no timestamps drifting).
- [ ] **Step 3:** 8 consecutive full runs green; planted unused PNG fails the guard.
- [ ] **Step 4:** Commit `test: add visual baselines`.

### Task 5: CI, hook, docs

**Files:**
- Create: `.github/workflows/frontend-e2e.yml`, `.githooks/pre-push`
- Modify: `README.md` or `AGENTS.md` (whichever holds dev docs) — Testing section

- [ ] **Step 1:** Workflow as website's `e2e.yml` (Dependabot-only, arm runner), caching `frontend/package-lock.json` and `uv.lock`, running `frontend/e2e/run.sh`.
- [ ] **Step 2:** Hook as website's, paths `^(frontend/|site/|tests/e2e/|src/guiltyspark/(dashboard|state|models|targets|clustering|config|github_pr)\.py$)`, running `npm --prefix frontend run test:e2e`.
- [ ] **Step 3:** Docs; `git config core.hooksPath .githooks`; verify hook skips a docs-only range and runs on a site range; YAML parses.
- [ ] **Step 4:** Commit `ci: run integration tests on Dependabot PRs and pre-push`.
