#!/usr/bin/env bash
# Container entrypoint for the CyberGym runner image.
#
# The group environment file is the same one the systemd units use. Two values
# are adjusted for container networking:
#   - PYTHON_BIN is the image interpreter, not the host virtualenv.
#   - The verification server must listen on all container interfaces so the
#     batch container can reach it by service name; SERVER_URL defaults to that
#     service name when the env file does not provide one.
set -euo pipefail

MODE="${1:---run}"
LAUNCHER="/opt/qitos-bench/qitos/qitos/benchmark/cybergym/agent/scripts/launch_v14_gdb_149.sh"

export PYTHON_BIN=/usr/local/bin/python

case "$MODE" in
  --server)
    # A group env file written for systemd usually sets 127.0.0.1; inside a
    # container that would be unreachable from the batch container.
    if [[ -z "${SERVER_HOST:-}" || "${SERVER_HOST}" == "127.0.0.1" ]]; then
      export SERVER_HOST=0.0.0.0
    fi
    ;;
  --run)
    # The verification server is started outside this compose project, so the
    # address must be explicit rather than derived from a service name.
    export SERVER_URL="${SERVER_URL:?SERVER_URL is required (point it at the verification server)}"
    ;;
  *)
    echo "Unsupported container mode: $MODE (expected --server or --run)" >&2
    exit 2
    ;;
esac

exec "$LAUNCHER" "$MODE"
