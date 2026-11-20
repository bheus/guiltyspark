#!/usr/bin/env bash
# Runs inside the e2e image (see run.sh). Works on a copy so the read-only
# source mount and .env are never touched.
set -euo pipefail

rsync -a --exclude .git --exclude .venv --exclude node_modules --exclude .worktrees \
  --exclude '*.sqlite3*' --exclude .env --exclude '.env.*' --exclude data --exclude .superpowers \
  --exclude frontend/playwright-report --exclude frontend/test-results \
  /src/ /work/
cd /work

uv sync --locked --quiet
(cd frontend && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent >/dev/null)

status=0
(cd frontend && npx playwright test "$@") || status=$?

rm -rf /out/playwright-report/* /out/test-results/*
cp -R frontend/playwright-report/. /out/playwright-report/ 2>/dev/null || true
cp -R frontend/test-results/. /out/test-results/ 2>/dev/null || true
cp -R frontend/e2e/__screenshots__/. /out/screenshots/ 2>/dev/null || true
exit "$status"
