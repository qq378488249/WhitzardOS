#!/usr/bin/env bash
# Score a CyberGym run inside Docker, so the host needs no Python.
#
# It reads the .env next to this script (or deploy/cybergym/.env in the repo
# layout) for SERVER_URL, TASK_FILE, and CYBERGYM_GRADING_KEY, then runs
# scripts/cybergym_server_score.py against the server's live /query-poc API.
#
# Usage:
#   bash deploy/cybergym/score.sh
#   bash deploy/cybergym/score.sh --since "2026-11-06 08:10:00"
#   bash deploy/cybergym/score.sh --env-file /path/to/.env --json
#
# WSL note: if the default `docker` shim cannot mount /home/..., use
#   DOCKER_BIN=/usr/bin/docker DOCKER_HOST=unix:///var/run/docker.sock \
#     bash deploy/cybergym/score.sh ...
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ -n "${SCORE_ENV_FILE:-}" ]]; then
  ENV_FILE="$SCORE_ENV_FILE"
elif [[ -f "$SCRIPT_DIR/.env" ]]; then
  ENV_FILE="$SCRIPT_DIR/.env"
else
  ENV_FILE="$ROOT/deploy/cybergym/.env"
fi
IMAGE="${SCORE_IMAGE:-qitos-cybergym-runner:local}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
SCORER=/opt/qitos-bench/qitos/scripts/cybergym_server_score.py
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="${2:?--env-file needs a path}"
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

[[ -f "$ENV_FILE" ]] || {
  echo "env file not found: $ENV_FILE" >&2
  echo "create it with: cp .env.compose.example .env (or pass --env-file)" >&2
  exit 2
}
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

: "${SERVER_URL:?SERVER_URL is empty in $ENV_FILE}"
: "${TASK_FILE:?TASK_FILE is empty in $ENV_FILE}"
: "${CYBERGYM_GRADING_KEY:?CYBERGYM_GRADING_KEY is empty in $ENV_FILE}"

exec "$DOCKER_BIN" run --rm \
  --env-file "$ENV_FILE" \
  --add-host=host.docker.internal:host-gateway \
  -v "$TASK_FILE:$TASK_FILE:ro" \
  --entrypoint python \
  "$IMAGE" \
  "$SCORER" \
  --server-url "$SERVER_URL" \
  --api-key "$CYBERGYM_GRADING_KEY" \
  --task-file "$TASK_FILE" \
  "${ARGS[@]}"
