from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from qitos.benchmark.common import read_benchmark_results
from qitos.core.spec import BenchmarkRunResult, ExperimentSpec, RunSpec
from qitos.recipes.benchmarks._shared import execute_example_jobs


def _row(
    task_id: str,
    *,
    success: bool = True,
    stop_reason: str | None = "success",
    prediction: Any = "poc.bin",
    latency_seconds: float = 0.1,
    metadata: dict[str, Any] | None = None,
) -> BenchmarkRunResult:
    merged: dict[str, Any] = {"job_key": task_id}
    merged.update(metadata or {})
    return BenchmarkRunResult(
        task_id=task_id,
        benchmark="demo",
        split="test",
        prediction=prediction,
        success=success,
        stop_reason=stop_reason,
        steps=1,
        latency_seconds=latency_seconds,
        token_usage=10,
        cost=0.0,
        trace_run_dir=None,
        run_spec_ref=None,
        metadata=merged,
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


def _report_lines(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_report_file_has_one_concise_line_per_job(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    report = tmp_path / "results.report.jsonl"

    execute_example_jobs(
        jobs=_jobs("ok-1", "ok-2"),
        runner=_runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
        report_path=report,
    )

    lines = _report_lines(report)
    assert [line["task_id"] for line in lines] == ["ok-1", "ok-2"]
    assert set(lines[0]) == {
        "task_id",
        "success",
        "stop_reason",
        "final_result",
        "elapsed_seconds",
    }
    assert lines[0]["final_result"] == "poc.bin"
    assert lines[0]["elapsed_seconds"] == 0.1


def test_report_line_carries_failure_reason_and_verdict_codes(
    tmp_path: Path,
) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    report = tmp_path / "results.report.jsonl"

    def runner(task, job_key=None, run_spec=None, experiment_spec=None):
        _ = job_key, run_spec, experiment_spec
        return _row(
            task,
            success=False,
            stop_reason="final",
            prediction=None,
            metadata={
                "failure_reason": "model returned unparseable tool-call output",
                "vul_exit_code": 139,
                "fix_exit_code": 1,
            },
        )

    execute_example_jobs(
        jobs=_jobs("ok-1"),
        runner=runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
        report_path=report,
    )

    line = _report_lines(report)[0]
    assert line["success"] is False
    assert line["stop_reason"] == "final"
    assert line["final_result"] is None
    assert line["reason"] == "model returned unparseable tool-call output"
    assert line["vul_exit_code"] == 139
    assert line["fix_exit_code"] == 1


def test_report_keeps_completed_job_before_later_failure(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    report = tmp_path / "results.report.jsonl"

    with pytest.raises(RuntimeError):
        execute_example_jobs(
            jobs=_jobs("ok-1", "boom"),
            runner=_runner,
            output_path=output,
            run_spec=run_spec,
            experiment_spec=experiment_spec,
            concurrency=1,
            report_path=report,
        )

    assert [line["task_id"] for line in _report_lines(report)] == ["ok-1"]


def test_resume_rebuilds_missing_report_from_existing_rows(tmp_path: Path) -> None:
    run_spec, experiment_spec = _specs()
    output = tmp_path / "results.jsonl"
    report = tmp_path / "results.report.jsonl"

    execute_example_jobs(
        jobs=_jobs("ok-1", "ok-2"),
        runner=_runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
    )
    execute_example_jobs(
        jobs=_jobs("ok-1", "ok-2"),
        runner=_runner,
        output_path=output,
        run_spec=run_spec,
        experiment_spec=experiment_spec,
        concurrency=1,
        resume=True,
        report_path=report,
    )

    assert [line["task_id"] for line in _report_lines(report)] == ["ok-1", "ok-2"]


def test_failure_reason_from_trace_prefers_failure_report(tmp_path: Path) -> None:
    from qitos.benchmark.cybergym.runner import _failure_reason_from_trace

    trace_dir = tmp_path / "trace"
    trace_dir.mkdir()
    (trace_dir / "manifest.json").write_text(
        json.dumps(
            {
                "summary": {
                    "failure_report": {
                        "failures": [{"message": "401 Unauthorized"}],
                    },
                    "last_error": {"error_message": "ignored"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert _failure_reason_from_trace(str(trace_dir)) == "401 Unauthorized"
    assert _failure_reason_from_trace("") == ""
