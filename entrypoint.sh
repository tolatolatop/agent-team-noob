#!/usr/bin/env sh
set -eu

if [ -n "${GITHUB_TOKEN:-}" ]; then
  printf '%s' "$GITHUB_TOKEN" | gh auth login --hostname github.com --with-token
fi

python -c "from team_noob.agent import run_service; import os; run_service(host='0.0.0.0', port=int(os.getenv('PORT', '8000')))"
