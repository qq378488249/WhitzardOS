#!/usr/bin/env bash
# Print a concise per-task report for one CyberGym batch run.
#
# Usage:
#   bash deploy/cybergym/report.sh [run_root_or_jsonl]
#
# With no argument the script uses $REPORT_JSONL when set, else the current
# directory (so `cd` into a run root and run it). A directory prefers its
# *.report.jsonl compact report and falls back to the newest full *.jsonl; a
# specific file can still be passed. Fields are read from either format, so
# runs made before REPORT_JSONL existed work too.
#
# The parser prefers host python3/python; when neither exists it falls back to
# the qitos-cybergym-runner image (host needs Docker only, same as score.sh).
set -euo pipefail

usage() {
  echo "usage: $0 [run_root_or_jsonl]" >&2
  exit 2
}

TARGET="${1:-${REPORT_JSONL:-$PWD}}"

if [[ -d "$TARGET" ]]; then
  TARGET="${TARGET%/}"
  mapfile -t reports < <(
    find "$TARGET" -maxdepth 1 -type f -name '*.report.jsonl' \
      -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-
  )
  if [[ ${#reports[@]} -gt 0 ]]; then
    candidates=("${reports[@]}")
  else
    mapfile -t candidates < <(
      find "$TARGET" -maxdepth 1 -type f -name '*.jsonl' ! -name '*.report.jsonl' \
        -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-
    )
  fi
  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "no *.report.jsonl or *.jsonl found under: $TARGET" >&2
    exit 1
  fi
  if [[ ${#candidates[@]} -gt 1 ]]; then
    echo "note: multiple result files, using newest:" >&2
    printf '  %s\n' "${candidates[@]}" >&2
  fi
  TARGET="${candidates[0]}"
fi

if [[ ! -f "$TARGET" ]]; then
  echo "not a file or directory: $TARGET" >&2
  exit 1
fi

parse() {
  "$@" - "$TARGET" <<'PY'
import json
import sys

path = sys.argv[1]
rows = []
with open(path, encoding="utf-8", errors="replace") as handle:
    for line in handle:
        text = line.strip()
        if not text:
            continue
        try:
            rows.append(json.loads(text))
        except json.JSONDecodeError:
            continue

if not rows:
    print("no rows in", path)
    sys.exit(0)


def clip(value, width):
    text = "" if value is None else str(value)
    return text if len(text) <= width else text[: width - 1] + "…"


def pick(row, key, default=None):
    value = row.get(key, default)
    return default if value in (None, "") else value


header = (
    f"{'TASK':<18} {'RESULT':<6} {'STOP':<13} {'TIME':>8} "
    f"{'FINAL RESULT':<24} {'VUL':>4} {'FIX':>4}  REASON"
)
print(header)
print("-" * len(header))

success_count = 0
for row in rows:
    compact = "elapsed_seconds" in row
    success = bool(row.get("success"))
    success_count += int(success)
    stop = pick(row, "stop_reason", "-")
    elapsed = pick(row, "elapsed_seconds" if compact else "latency_seconds", 0)
    final = pick(row, "final_result" if compact else "prediction")
    meta = row.get("metadata") or {}
    vul = row.get("vul_exit_code", meta.get("vul_exit_code"))
    fix = row.get("fix_exit_code", meta.get("fix_exit_code"))
    reason = row.get("reason") or meta.get("failure_reason") or ""
    if not success and not reason:
        reason = stop
    print(
        f"{clip(row.get('task_id'), 18):<18} "
        f"{'ok' if success else 'FAIL':<6} "
        f"{clip(stop, 13):<13} "
        f"{float(elapsed or 0):>7.1f}s "
        f"{clip(final, 24):<24} "
        f"{'-' if vul is None else str(vul):>4} "
        f"{'-' if fix is None else str(fix):>4}  "
        f"{clip(reason, 48) if not success else ''}"
    )

total = len(rows)
rate = (success_count / total * 100) if total else 0.0
print("-" * len(header))
print(f"total={total} success={success_count} rate={rate:.1f}%")
PY
}

python_ok() {
  "$1" -c 'import sys' >/dev/null 2>&1
}

if command -v python3 >/dev/null 2>&1 && python_ok python3; then
  parse python3
elif command -v python >/dev/null 2>&1 && python_ok python; then
  parse python
elif command -v docker >/dev/null 2>&1 \
  && docker image inspect qitos-cybergym-runner:local >/dev/null 2>&1; then
  mount_dir="$(dirname "$TARGET")"
  parse docker run --rm -v "$mount_dir:$mount_dir:ro" \
    --entrypoint python qitos-cybergym-runner:local
else
  echo "need python3/python on the host, or the qitos-cybergym-runner:local image in Docker" >&2
  exit 1
fi
