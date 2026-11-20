#!/usr/bin/env bash
# Run the Playwright suite in the pinned linux/arm64 container.
# Usage: frontend/e2e/run.sh [playwright test args…]   e.g. --update-snapshots
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
version="$(node -p "require('$repo/frontend/package-lock.json').packages['node_modules/@playwright/test'].version")"
image="guiltyspark-e2e:$version"

docker build --quiet --platform linux/arm64 --build-arg "PLAYWRIGHT_VERSION=$version" \
  -t "$image" "$repo/frontend/e2e" >/dev/null

# Caches persist between runs: named volumes locally, host dirs in CI.
if [[ -n "${E2E_CACHE_DIR:-}" ]]; then
  mkdir -p "$E2E_CACHE_DIR/npm" "$E2E_CACHE_DIR/uv"
  caches=(-v "$E2E_CACHE_DIR/npm:/root/.npm" -v "$E2E_CACHE_DIR/uv:/root/.cache/uv")
else
  caches=(-v guiltyspark-e2e-npm:/root/.npm -v guiltyspark-e2e-uv:/root/.cache/uv)
fi

tty=()
[[ -t 0 && -t 1 ]] && tty=(-it)

mkdir -p "$repo/frontend/e2e/__screenshots__" "$repo/frontend/playwright-report" "$repo/frontend/test-results"
exec docker run --rm --init --ipc=host --platform linux/arm64 ${tty[@]+"${tty[@]}"} \
  -v "$repo:/src:ro" \
  -v "$repo/frontend/e2e/__screenshots__:/out/screenshots" \
  -v "$repo/frontend/playwright-report:/out/playwright-report" \
  -v "$repo/frontend/test-results:/out/test-results" \
  "${caches[@]}" \
  -e CI="${CI:-}" -e E2E_CONTAINER=1 \
  "$image" bash /src/frontend/e2e/in-container.sh "$@"
