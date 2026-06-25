#!/bin/sh
set -e

if [ -n "${CODEX_HOME:-}" ]; then
  mkdir -p "$CODEX_HOME"
fi

exec "$@"
