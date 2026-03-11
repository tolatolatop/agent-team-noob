#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-team-noob:latest}"
BUILD_TARGET="${BUILD_TARGET:-deploy}"
CONTAINER_NAME="${CONTAINER_NAME:-team-noob}"
HOST_PORT="${HOST_PORT:-8000}"
PORT="${PORT:-8000}"
HEALTHCHECK_MAX_RETRIES="${HEALTHCHECK_MAX_RETRIES:-15}"
HEALTHCHECK_INTERVAL_SECONDS="${HEALTHCHECK_INTERVAL_SECONDS:-1}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found, please install Docker first." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "curl command not found, please install curl first." >&2
  exit 1
fi

echo "[1/5] Building image: ${IMAGE_NAME}"
docker build --target "${BUILD_TARGET}" -t "${IMAGE_NAME}" .

if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "[2/5] Removing existing container: ${CONTAINER_NAME}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null
else
  echo "[2/5] No existing container found."
fi

RUN_ARGS=(
  -d
  --name "${CONTAINER_NAME}"
  -p "${HOST_PORT}:${PORT}"
  -e "PORT=${PORT}"
)

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  RUN_ARGS+=(-e "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
else
  echo "warning: ANTHROPIC_API_KEY is not set; Claude requests may fail at runtime." >&2
fi

echo "[3/5] Starting container: ${CONTAINER_NAME}"
docker run "${RUN_ARGS[@]}" "${IMAGE_NAME}" >/dev/null

echo "[4/5] Container status:"
docker ps --filter "name=^${CONTAINER_NAME}$" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
echo "Service endpoint: http://127.0.0.1:${HOST_PORT}/notify"

echo "[5/5] Waiting for /notify health check..."
HEALTH_URL="http://127.0.0.1:${HOST_PORT}/notify"
HEALTH_PAYLOAD='{"pipeline":"default","message":{"content":"deploy health check"}}'

for ((i = 1; i <= HEALTHCHECK_MAX_RETRIES; i++)); do
  if RESPONSE="$(curl -sS -X POST "${HEALTH_URL}" -H "Content-Type: application/json" -d "${HEALTH_PAYLOAD}" 2>/dev/null)"; then
    if [[ "${RESPONSE}" == *'"ok": true'* || "${RESPONSE}" == *'"ok":true'* ]]; then
      echo "Health check passed on attempt ${i}: ${RESPONSE}"
      exit 0
    fi
  fi
  sleep "${HEALTHCHECK_INTERVAL_SECONDS}"
done

echo "Health check failed after ${HEALTHCHECK_MAX_RETRIES} attempts: ${HEALTH_URL}" >&2
docker logs "${CONTAINER_NAME}" --tail 80 || true
exit 1
