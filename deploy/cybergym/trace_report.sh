#!/usr/bin/env bash
# Explain each finished task in a CyberGym traces directory.
#
# Usage:
#   cd <run_root>/traces
#   bash /path/to/deploy/cybergym/trace_report.sh [traces_dir]
#
# Reads every */manifest.json and prints one row per *finished* run: success or
# failure, stop reason, elapsed time, steps, and the failure reason (trace
# failure_report, then last_error, then the stop reason). Runs whose status is
# still "running" are skipped. The parser prefers host python3/python and
# falls back to qitos-cybergym-runner:local, like report.sh.
set -euo pipefail

usage() {
  echo "usage: $0 [traces_dir]" >&2
  exit 2
}

TARGET="${1:-$PWD}"

if [[ ! -d "$TARGET" ]]; then
  echo "not a directory: $TARGET" >&2
  exit 1
fi

parse() {
  "$@" - "$TARGET" <<'PY'
import json
import os
import re
import sys

root = sys.argv[1]


def clip(value, width):
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def summarize_result(value):
    text = "" if value is None else str(value)
    match = re.search(r"pocs/[\w./-]+", text)
    if match:
        return match.group(0)
    return " ".join(text.split())


def task_id_of(name, manifest, summary, task_result):
    meta = summary.get("task_meta")
    if isinstance(meta, dict) and meta.get("task_id"):
        return str(meta["task_id"])
    tr_meta = (task_result.get("metadata") or {}).get("task_meta")
    if isinstance(tr_meta, dict) and tr_meta.get("task_id"):
        return str(tr_meta["task_id"])
    return name


def failure_reason(summary, stop):
    """Prefer the failure that actually stopped the run.

    failure_report keeps both recovered (`decision=continue`) and terminal
    (`decision=stop`) failures. Showing the last entry alone can blame a
    retried 503 for a run that really ended on budget_time, so terminal
    failures win; if every recorded failure was recovered, the stop reason is
    the primary cause and the last error becomes a hint.
    """
    failures = []
    report = summary.get("failure_report")
    if isinstance(report, dict):
        raw_failures = report.get("failures")
        if isinstance(raw_failures, list):
            failures = [item for item in raw_failures if isinstance(item, dict)]

    def describe(failure):
        parts = [
            failure.get("category"),
            failure.get("phase"),
            failure.get("message"),
        ]
        return " / ".join(str(part) for part in parts if part)

    def describe_short(failure):
        message = str(failure.get("message") or "").strip()
        return message or str(failure.get("category") or "").strip()

    terminal = [
        failure
        for failure in failures
        if str(failure.get("decision") or "").lower() == "stop"
    ]
    if terminal:
        text = describe(terminal[-1])
        if text:
            return text

    last_text = ""
    if failures:
        last_text = describe_short(failures[-1])
    if not last_text:
        last_error = summary.get("last_error")
        if isinstance(last_error, dict) and last_error.get("error_message"):
            last_text = str(last_error["error_message"])

    stop_text = str(stop or "").strip()
    if last_text:
        if stop_text and stop_text not in ("unknown", "final", "-"):
            return f"{stop_text}; last: {last_text}"
        return last_text
    return ""


def last_decision_source(trace_dir):
    """decision_source of the final StepRecord (read the tail of steps.jsonl)."""
    path = os.path.join(trace_dir, "steps.jsonl")
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - 262144))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed(data.splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        source = record.get("decision_source")
        if source:
            return str(source)
        decision = record.get("decision")
        if isinstance(decision, dict) and decision.get("mode"):
            return str(decision["mode"])
    return ""


def final_reason(trace_dir, summary):
    """Explain a self-declared `final`: model answer vs unparseable tool call."""
    source = last_decision_source(trace_dir)
    parser = summary.get("parser")
    if not isinstance(parser, dict):
        parser = {}
    codes = parser.get("error_codes")
    if not isinstance(codes, dict):
        codes = {}
    code_text = ", ".join(f"{key} x{value}" for key, value in codes.items())
    final_text = str(summary.get("final_result") or "")
    if "DSML" in final_text:
        return "final: leaked DSML tool-call markup ended the run"
    if source == "native_text_tool_markup":
        return "final: leaked tool-call markup (DSML)"
    if source == "native_text_final_degraded":
        suffix = f" ({code_text})" if code_text else ""
        return "final: repeated unparseable tool-call text (degraded)" + suffix
    if source == "native_text_final" and (parser.get("error_count") or code_text):
        suffix = f" ({code_text})" if code_text else ""
        return "final: model tool-call text could not be parsed" + suffix
    if source == "native_text_final":
        return "model declared final (no tool call)"
    if code_text:
        return f"model declared final; parser errors: {code_text}"
    return "model declared final (check server verdict)"


rows = []
running = 0
unreadable = 0
for name in sorted(os.listdir(root)):
    path = os.path.join(root, name)
    if not os.path.isdir(path):
        continue
    manifest_path = os.path.join(path, "manifest.json")
    if not os.path.isfile(manifest_path):
        continue
    try:
        with open(manifest_path, encoding="utf-8", errors="replace") as handle:
            manifest = json.load(handle)
    except (OSError, json.JSONDecodeError):
        unreadable += 1
        continue
    if not isinstance(manifest, dict):
        unreadable += 1
        continue
    summary = manifest.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    status = str(manifest.get("status") or "").lower()
    stop = summary.get("stop_reason")
    if status == "running" or (not stop and status not in ("completed", "failed")):
        running += 1
        continue

    task_result = summary.get("task_result")
    if not isinstance(task_result, dict):
        task_result = {}
    success = bool(task_result.get("success"))
    metrics = task_result.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    elapsed = summary.get("latency_seconds")
    if elapsed is None:
        elapsed = metrics.get("elapsed_seconds")
    try:
        elapsed_value = float(elapsed or 0)
    except (TypeError, ValueError):
        elapsed_value = 0.0
    steps = summary.get("steps")
    if steps is None:
        steps = metrics.get("steps")
    try:
        steps_value = int(steps or 0)
    except (TypeError, ValueError):
        steps_value = 0

    reason = failure_reason(summary, stop)
    if str(stop) == "final":
        reason = final_reason(path, summary)
    elif success:
        final_result = summarize_result(summary.get("final_result"))
        reason = "verified" + (f": {final_result}" if final_result else "")
    elif not reason:
        reason = str(stop or "unknown")
    rows.append(
        (
            task_id_of(name, manifest, summary, task_result),
            "ok" if success else "FAIL",
            str(stop or "-"),
            elapsed_value,
            steps_value,
            reason,
        )
    )

if not rows:
    print(f"no finished traces in {root} (running skipped: {running})")
    sys.exit(0)

header = (
    f"{'TASK':<18} {'RESULT':<6} {'STOP':<20} {'TIME':>8} {'STEPS':>6}  REASON"
)
print(header)
print("-" * len(header))
for task_id, result, stop, elapsed, steps, reason in rows:
    print(
        f"{clip(task_id, 18):<18} {result:<6} {clip(stop, 20):<20} "
        f"{elapsed:>7.1f}s {steps:>6}  {clip(reason, 70)}"
    )
print("-" * len(header))
ok = sum(1 for row in rows if row[1] == "ok")
print(
    f"finished={len(rows)} success={ok} failed={len(rows) - ok} "
    f"running_skipped={running}"
    + (f" unreadable={unreadable}" if unreadable else "")
)
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
  parse docker run --rm -v "$TARGET:$TARGET:ro" \
    --entrypoint python qitos-cybergym-runner:local
else
  echo "need python3/python on the host, or the qitos-cybergym-runner:local image in Docker" >&2
  exit 1
fi
