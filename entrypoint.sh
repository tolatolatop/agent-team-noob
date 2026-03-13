#!/usr/bin/env sh
set -eu

if [ -n "${GITHUB_TOKEN:-}" ]; then
  printf '%s' "$GITHUB_TOKEN" | gh auth login --hostname github.com --with-token
fi

init_after_10s() {
  sleep 10
  curl -X POST "http://127.0.0.1:8000/notify" \
    -H "Content-Type: application/json" \
    --data-binary @- <<'JSON'
{"pipeline":"default","message":{"content":"read CLAUDE.md and complete the following tasks:\n1. read the CLAUDE.md file\n2. complete the following tasks:"}}
JSON
}

init_after_10s &

python src/team_noob/start_services.py
