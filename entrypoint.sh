#!/usr/bin/env sh
set -eu

if [ -n "${GITHUB_TOKEN:-}" ]; then
  printf '%s' "$GITHUB_TOKEN" | gh auth login --hostname github.com --with-token
fi

exec python - <<'PY'
import os
from team_noob.agent import run_service

host = os.getenv("HOST", "0.0.0.0")
port = int(os.getenv("PORT", "8000"))

run_service(host=host, port=port)
PY
