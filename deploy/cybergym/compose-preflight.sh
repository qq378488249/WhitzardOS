#!/usr/bin/env bash
# Preflight checks for the Docker Compose CyberGym batch deployment.
#
# Usage:
#   bash deploy/cybergym/compose-preflight.sh [env-file]
#
# The argument defaults to deploy/cybergym/.env. The script only reads files,
# inspects images, and creates RUN_ROOT; it starts nothing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-${ROOT}/deploy/cybergym/.env}"

fail() { echo "FAIL: $*" >&2; exit 1; }
warn() { echo "WARN: $*" >&2; }

load_env() {
  # Parses simple KEY=value lines. CRLF and surrounding quotes are tolerated.
  local file=$1 line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%$'\r'}"
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    printf -v "$key" '%s' "$value"
  done < "$file"
}

command -v docker >/dev/null 2>&1 || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose is not available"
[[ -f "$ENV_FILE" ]] || fail "env file not found: $ENV_FILE (copy deploy/cybergym/.env.compose.example)"
load_env "$ENV_FILE"

for var in RUN_ROOT CYBERGYM_ROOT DATA_DIR BINARY_DIR TASK_FILE SERVER_URL BASE_URL LLM_API_KEY CYBERGYM_GRADING_KEY; do
  [[ -n "${!var:-}" ]] || fail "$var is empty (set it in $ENV_FILE)"
done

for var in RUN_ROOT CYBERGYM_ROOT DATA_DIR BINARY_DIR TASK_FILE; do
  case "${!var}" in
    /*) ;;
    *) fail "$var must be an absolute path, got: ${!var}" ;;
  esac
done

[[ "$SERVER_URL" =~ ^https?:// ]] || fail "SERVER_URL must start with http:// or https://, got: $SERVER_URL"
[[ "$BASE_URL" == */v1 ]] || warn "BASE_URL does not end with /v1: $BASE_URL (the OpenAI client does not append it)"
[[ -f "$CYBERGYM_ROOT/src/cybergym/task/README.template" ]] \
  || fail "CYBERGYM_ROOT does not look like a CyberGym checkout: $CYBERGYM_ROOT"
[[ -d "$DATA_DIR" ]] || fail "DATA_DIR does not exist: $DATA_DIR"
[[ -d "$BINARY_DIR" ]] || fail "BINARY_DIR does not exist: $BINARY_DIR"
[[ -f "$TASK_FILE" ]] || fail "TASK_FILE does not exist: $TASK_FILE"
[[ -s "$TASK_FILE" ]] || fail "TASK_FILE is empty: $TASK_FILE"

mkdir -p "$RUN_ROOT" || fail "cannot create RUN_ROOT: $RUN_ROOT"
[[ -w "$RUN_ROOT" ]] || fail "RUN_ROOT is not writable by $(id -un): $RUN_ROOT"
mkdir -p "$RUN_ROOT/server_poc"

docker image inspect cage/claude-code:cyberdebug >/dev/null 2>&1 \
  || fail "agent image cage/claude-code:cyberdebug is missing; docker load deploy/images/cage-claude-code-cyberdebug.tar.gz"
docker image inspect qitos-cybergym-runner:local >/dev/null 2>&1 \
  || fail "runner image qitos-cybergym-runner:local is missing; docker load deploy/images/qitos-cybergym-runner-1.0.4.tar.gz"
echo "runner image: qitos-cybergym-runner:local (present)"

if [[ "$SERVER_URL" == *"host.docker.internal"* ]]; then
  echo "server      : $SERVER_URL (resolved inside the container; host-side check skipped)"
elif command -v curl >/dev/null 2>&1; then
  if curl -sS -o /dev/null --max-time 5 "$SERVER_URL"; then
    echo "server      : reachable at $SERVER_URL"
  else
    warn "verification server is not reachable at $SERVER_URL (start it before the batch)"
  fi
fi

echo "OK: preflight passed"
echo "  env file  : $ENV_FILE"
echo "  run root  : $RUN_ROOT"
echo "  tasks     : $(grep -cve '^[[:space:]]*$' "$TASK_FILE")"
echo "next:"
echo "  docker compose --env-file $ENV_FILE -f $ROOT/deploy/cybergym/compose.yaml up -d"
