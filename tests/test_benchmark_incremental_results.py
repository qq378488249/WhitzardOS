from __future__ import annotations

import json
from pathlib import Path

import pytest

from qitos.benchmark.common import read_benchmark_results
from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec
from qitos.recipes.benchmarks._shared import execute_example_jobs


def _row(task_id: str) -> BenchmarkRunResult:
    return BenchmarkRunResult(
        task_id=task_id,
        benchmark="demo",
        split="test",
        prediction="poc.bin",
        success=True,
        stop_reason="success",
        steps=1,
        latency_seconds=0.1,
        token_usage=10,
        cost=0.0,
        trace_run_dir=None,
        run_spec_ref=None,
        metadata={"job_key": task_id},
    )


def _runner(task, job_key=None, run_spec=None, experiment_spec=None):
    _ = job_key, run_spec, experiment_spec
    if task == "boom":
        raise RuntimeError("boom")
    return _row(task)


def _specs() -> tuple[RunSpec, ExperimentSpec]:
    return RunSpec.infer(model_name="demo-model"), ExperimentSpec(name="demo")


def _jobs(*task_ids: str) -> list[dict]:
    return [{"task": task_id, "job_key": task_id} for task_id in task_ids]


def test_completed_jobs_are_persisted_before_a_later_failure(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"

    with pytest.raises(RuntimeError):
        execute_example_jobs(
            jobs=_jobs("ok-1", "boom"),
            runner=_runner,
            output_path=output,
            run_spec=run_spec,
            experiment_spec=experiment_spec,
            concurrency=1,
        )

    assert [row.task_id for row in read_benchmark_results(output)] == ["ok-1"]


def test_resume_skips_rows_already_on_disk(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    calls: list[str] = []

    def runner(task, job_key=None, run_spec=None, experiment_spec=None):
        _ = job_key, run_spec, experiment_spec
        calls.append(task)
        return _row(task)

    execute_example_jobs(
        jobs=_jobs("ok-1", "ok-2"),
        runner=runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
    )
    execute_example_jobs(
        jobs=_jobs("ok-1", "ok-2", "ok-3"),
        runner=runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
        resume=True,
    )

    assert calls == ["ok-1", "ok-2", "ok-3"]
    assert sorted(row.task_id for row in read_benchmark_results(output)) == [
        "ok-1",
        "ok-2",
        "ok-3",
    ]


def test_concurrent_jobs_are_appended_exactly_once(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    task_ids = [f"task-{index}" for index in range(6)]

    execute_example_jobs(
        jobs=_jobs(*task_ids),
        runner=_runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=3,
    )

    rows = read_benchmark_results(output)
    assert sorted(row.task_id for row in rows) == sorted(task_ids)
    assert len(rows) == len(task_ids)


def test_non_resume_run_replaces_previous_file(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    output.write_text('{"stale": true}\n', encoding="utf-8")

    execute_example_jobs(
        jobs=_jobs("ok-1"),
        runner=_runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
    )

    rows = read_benchmark_results(output)
    assert [row.task_id for row in rows] == ["ok-1"]


def test_reader_ignores_a_truncated_final_line(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    output.write_text(
        json.dumps(_row("ok-1").to_dict(), ensure_ascii=False)
        + "\n"
        + '{"task_id": "ok-2", "benchmark": "demo"',
        encoding="utf-8",
    )

    rows = read_benchmark_results(output)

    assert [row.task_id for row in rows] == ["ok-1"]
