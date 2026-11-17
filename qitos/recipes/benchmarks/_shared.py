"""Shared benchmark recipe helpers for the canonical QitOS benchmark flow."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from qitos.benchmark.common import (
    append_benchmark_result,
    append_task_report,
    build_experiment_spec,
    evaluate_benchmark_results,
    read_benchmark_results,
    write_benchmark_results,
)
from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec

RecipeRunner = Callable[..., BenchmarkRunResult | Dict[str, Any]]


def build_example_specs(
    *,
    benchmark: str,
    split: str,
    model_name: Optional[str],
    trace_logdir: str,
    parser_name: str = "ReActTextParser",
    toolset_name: Optional[str] = None,
    subset: Optional[str] = None,
    seed: Optional[int] = None,
    limit: Optional[int] = None,
    workspace: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> tuple[RunSpec, ExperimentSpec]:
    run_spec = RunSpec.infer(
        model_name=model_name,
        prompt_protocol="react_text_v1",
        parser_name=parser_name,
        toolset_name=toolset_name,
        benchmark_name=benchmark,
        benchmark_split=split,
        trace_schema_version="v1",
        seed=seed,
        environment={
            "trace_logdir": str(trace_logdir),
            "workspace": str(workspace or ""),
        },
        metadata=dict(metadata or {}),
    )
    experiment_spec = build_experiment_spec(
        benchmark=benchmark,
        split=split,
        run_spec=run_spec,
        subset=subset,
        limit=limit,
    )
    return run_spec, experiment_spec


def default_output_path(
    root: str | Path, *, benchmark: str, split: str, suffix: str = ".jsonl"
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path(root).expanduser().resolve() / f"{benchmark}_{split}_{stamp}{suffix}"


def execute_example_jobs(
    *,
    jobs: Iterable[Dict[str, Any]],
    runner: RecipeRunner,
    output_path: str | Path,
    run_spec: RunSpec,
    experiment_spec: ExperimentSpec,
    concurrency: int = 1,
    resume: bool = False,
    report_path: str | Path | None = None,
    retry_stop_reasons: Optional[Iterable[str]] = None,
    max_attempts: int = 3,
) -> List[BenchmarkRunResult]:
    retry_reasons = {str(reason) for reason in (retry_stop_reasons or ())}
    if retry_reasons and int(max_attempts) < 1:
        raise ValueError("max_attempts must be >= 1 when retry_stop_reasons is set")
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    report_target = (
        Path(report_path).expanduser().resolve() if report_path is not None else None
    )
    job_items = list(jobs)
    if resume:
        existing_rows = (
            read_benchmark_results(target) if target.exists() else []
        )
        if retry_reasons:
            # A retried task can leave an old and a new row on disk after a
            # crash; keep the newest one before deciding what is still pending.
            existing_rows = _dedupe_rows(existing_rows)
    else:
        # A fresh run owns the file, matching the previous overwrite behavior.
        existing_rows = []
        with target.open("w", encoding="utf-8"):
            pass
    if report_target is not None:
        report_target.parent.mkdir(parents=True, exist_ok=True)
        if not resume:
            with report_target.open("w", encoding="utf-8"):
                pass
        elif not report_target.exists():
            # Rebuild the compact report from rows already on disk.
            for row in existing_rows:
                append_task_report(report_target, row)
    done: set[str] = set()
    attempts_by_key: Dict[str, int] = {}
    for row in existing_rows:
        key = _row_job_key(row)
        attempts = _row_attempts(row)
        attempts_by_key[key] = max(attempts_by_key.get(key, 0), attempts)
        retryable = bool(
            retry_reasons
            and not row.success
            and str(row.stop_reason or "") in retry_reasons
            and attempts < int(max_attempts)
        )
        if not retryable:
            done.add(key)
    pending: List[tuple[Dict[str, Any], Optional[int]]] = []
    for item in job_items:
        key = _item_job_key(item)
        if key in done:
            continue
        attempt = attempts_by_key.get(key, 0) + 1 if retry_reasons else None
        pending.append((item, attempt))

    all_rows: List[BenchmarkRunResult] = list(existing_rows)
    write_lock = threading.Lock()

    def _record(item: Dict[str, Any], attempt: Optional[int]) -> BenchmarkRunResult:
        row = _run_example_job(
            runner, item, run_spec, experiment_spec, attempt=attempt
        )
        # Persist each finished job immediately so a crash keeps every row
        # that already completed; --resume then skips exactly those jobs.
        with write_lock:
            append_benchmark_result(target, row)
            if report_target is not None:
                append_task_report(report_target, row)
            all_rows.append(row)
        return row

    if int(concurrency) <= 1:
        for item, attempt in pending:
            _record(item, attempt)
    else:
        with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as pool:
            futures = [
                pool.submit(_record, item, attempt) for item, attempt in pending
            ]
            for future in as_completed(futures):
                # Raises the first failure; already-finished jobs are on disk.
                future.result()

    if retry_reasons:
        # Retries append rows, so fold each task to its newest result once the
        # run ends. Input in the middle of the run stays append-only for crash
        # safety; the next run converges on the same newest row if it dies.
        all_rows = _dedupe_rows(all_rows)
        _rewrite_results(target, all_rows)
        if report_target is not None:
            _rewrite_report(report_target, all_rows)

    return all_rows


def print_benchmark_summary(rows: Iterable[BenchmarkRunResult]) -> None:
    summary = evaluate_benchmark_results(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def print_single_result(row: BenchmarkRunResult) -> None:
    print(json.dumps(row.to_dict(), ensure_ascii=False, indent=2))


def _item_job_key(item: Dict[str, Any]) -> str:
    return str(item.get("job_key", "") or item.get("task_id", "")).strip()


def _row_job_key(row: BenchmarkRunResult) -> str:
    return (
        str((row.metadata or {}).get("job_key", "")).strip()
        or str(row.task_id).strip()
    )


def _row_attempts(row: BenchmarkRunResult) -> int:
    raw = (row.metadata or {}).get("attempts", 1)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _dedupe_rows(rows: Iterable[BenchmarkRunResult]) -> List[BenchmarkRunResult]:
    """Keep the newest row per task; retries append newer rows."""
    seen: set[str] = set()
    deduped: List[BenchmarkRunResult] = []
    for row in reversed(list(rows)):
        key = _row_job_key(row)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    deduped.reverse()
    return deduped


def _rewrite_results(path: Path, rows: Iterable[BenchmarkRunResult]) -> None:
    temp = path.with_name(path.name + ".tmp")
    write_benchmark_results(temp, rows)
    os.replace(temp, path)


def _rewrite_report(path: Path, rows: Iterable[BenchmarkRunResult]) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8"):
        pass
    for row in rows:
        append_task_report(temp, row)
    os.replace(temp, path)


def _run_example_job(
    runner: RecipeRunner,
    item: Dict[str, Any],
    run_spec: RunSpec,
    experiment_spec: ExperimentSpec,
    *,
    attempt: Optional[int] = None,
) -> BenchmarkRunResult:
    # Set sticky-routing key so the upstream load balancer routes all
    # LLM requests for this task to the same backend node.  This improves
    # KV-cache hit rate and reduces latency jitter.
    _prev_key = os.environ.get("QITOS_INFERENCE_KEY")
    job_key = str(item.get("job_key") or item.get("task_id") or "")
    if job_key:
        os.environ["QITOS_INFERENCE_KEY"] = job_key

    try:
        produced = runner(
            **item,
            run_spec=run_spec,
            experiment_spec=experiment_spec,
        )
    finally:
        if _prev_key is not None:
            os.environ["QITOS_INFERENCE_KEY"] = _prev_key
        else:
            os.environ.pop("QITOS_INFERENCE_KEY", None)
    row = BenchmarkRunResult.from_value(produced)
    metadata = dict(row.metadata or {})
    if item.get("job_key") is not None:
        metadata.setdefault("job_key", item.get("job_key"))
    if attempt is not None:
        metadata["attempts"] = max(1, int(attempt))
    row.metadata = metadata
    if not row.run_spec_ref:
        row.run_spec_ref = run_spec.fingerprint()
    return row
