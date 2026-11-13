"""Benchmark-generic task/result helpers that do not depend on family runners."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec
from qitos.tracing.config import _redact_dict

_logger = logging.getLogger(__name__)


def build_experiment_spec(
    *,
    benchmark: str,
    split: str,
    run_spec: RunSpec,
    subset: Optional[str] = None,
    limit: Optional[int] = None,
    judge_config: Optional[Dict[str, Any]] = None,
) -> ExperimentSpec:
    name = f"{benchmark}:{split}"
    if subset:
        name = f"{name}:{subset}"
    return ExperimentSpec(
        name=name,
        benchmark_name=benchmark,
        benchmark_split=split,
        judge_config=dict(judge_config or {}),
        benchmark_metadata={
            "subset": subset,
            "limit": limit,
        },
        run_defaults={
            "run_spec": run_spec.to_dict(),
        },
    )


def write_benchmark_results(
    path: str | Path, rows: Iterable[BenchmarkRunResult]
) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(_redact_dict(row.to_dict()), ensure_ascii=False))
            f.write("\n")
    return target


def append_benchmark_result(path: str | Path, row: BenchmarkRunResult) -> Path:
    """Append one result row and flush it to disk immediately.

    Batch runners call this as each job finishes, so a crash keeps every
    result that already completed. The row is a single JSON line, executed
    under the caller's lock; concurrent writers to the same file are not
    supported.
    """
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(_redact_dict(row.to_dict()), ensure_ascii=False) + "\n"
    with target.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return target


def append_task_report(path: str | Path, row: BenchmarkRunResult) -> Path:
    """Append a compact one-line task report: identifier, outcome, and time.

    Written alongside the full result row once per finished job, so operators
    can tail the file and see each task's final result and elapsed time
    without reading the full trace row.
    """
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    final_result = row.prediction
    if final_result is not None and not isinstance(
        final_result, (str, int, float, bool)
    ):
        final_result = json.dumps(_redact_dict(final_result), ensure_ascii=False)
    payload = {
        "task_id": row.task_id,
        "success": bool(row.success),
        "stop_reason": row.stop_reason,
        "final_result": final_result,
        "elapsed_seconds": round(float(row.latency_seconds or 0.0), 2),
    }
    if not row.success:
        payload["reason"] = (
            str(row.metadata.get("failure_reason") or "").strip()
            or str(row.stop_reason or "").strip()
            or "unknown"
        )
    for key in ("vul_exit_code", "fix_exit_code"):
        value = row.metadata.get(key)
        if value is not None:
            payload[key] = value
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with target.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return target


def read_benchmark_results(path: str | Path) -> list[BenchmarkRunResult]:
    rows: list[BenchmarkRunResult] = []
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return rows
    lines = target.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # A crash between write and fsync can leave a partial final line.
            # Keep the fully written rows before it instead of failing resume.
            if index == len(lines) - 1:
                _logger.warning(
                    "Ignoring truncated trailing result line in %s", target
                )
                break
            raise
        rows.append(BenchmarkRunResult.from_value(payload))
    return rows


def evaluate_benchmark_results(rows: Iterable[BenchmarkRunResult]) -> Dict[str, Any]:
    items = list(rows)
    total = len(items)
    success_count = sum(1 for item in items if item.success)
    avg_steps = (
        sum(int(item.steps or 0) for item in items) / total if total else 0.0
    )
    avg_latency = (
        sum(float(item.latency_seconds or 0.0) for item in items) / total
        if total
        else 0.0
    )
    avg_tokens = (
        sum(int(item.token_usage or 0) for item in items) / total if total else 0.0
    )
    total_cost = sum(float(item.cost or 0.0) for item in items)
    stop_reasons: Dict[str, int] = {}
    failure_tags: Dict[str, int] = {}
    for item in items:
        key = str(item.stop_reason or "unknown")
        stop_reasons[key] = stop_reasons.get(key, 0) + 1
        meta = dict(item.metadata or {})
        for tag in list(meta.get("failure_tags") or []):
            token = str(tag or "").strip()
            if not token:
                continue
            failure_tags[token] = failure_tags.get(token, 0) + 1
    benchmark = items[0].benchmark if items else None
    split = items[0].split if items else None
    return {
        "benchmark": benchmark,
        "split": split,
        "total": total,
        "success_count": success_count,
        "success_rate": (float(success_count) / float(total)) if total else 0.0,
        "avg_steps": avg_steps,
        "avg_latency_seconds": avg_latency,
        "avg_token_usage": avg_tokens,
        "total_cost": total_cost,
        "stop_reason_distribution": stop_reasons,
        "failure_tag_distribution": failure_tags,
    }


__all__ = [
    "build_experiment_spec",
    "evaluate_benchmark_results",
    "read_benchmark_results",
    "write_benchmark_results",
]
