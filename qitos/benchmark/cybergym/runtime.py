"""CyberGym benchmark runtime helpers."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from qitos.core import ExperimentSpec, RunSpec, Task

from ..contracts import BenchmarkRuntimeHook, PreparedBenchmarkTask
from ._imports import ensure_cybergym_source_importable


def _resolve_mask_map_path() -> Path | None:
    """Return the task-id mask map configured for this run, if any.

    Servers started with ``--mask_map_path`` expect submissions to carry the
    masked task id; the task package must therefore be generated with the
    same mask map or every ``submit_poc`` fails with ``Invalid task_id``.
    """
    raw = str(
        os.getenv("CYBERGYM_MASK_MAP") or os.getenv("CYBERGYM_MASK_MAP_PATH") or ""
    ).strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"CYBERGYM_MASK_MAP points to a missing file: {path}")
    return path


def prepare_task_dir(
    *,
    task_id: str,
    out_dir: str | Path,
    data_dir: str | Path,
    server: str,
    difficulty: str,
    fresh: bool = True,
) -> Path:
    ensure_cybergym_source_importable()
    from cybergym.task.gen_task import generate_task
    from cybergym.task.types import TaskConfig, TaskDifficulty

    out_path = Path(out_dir).expanduser().resolve()
    # Start each (re)run from a clean task dir so agent artifacts from a prior
    # run (PoCs, scratch files, .agent/ memory) can't pollute it. Safe under
    # --resume: completed tasks are filtered out before this is ever called.
    if fresh and out_path.exists():
        shutil.rmtree(out_path, ignore_errors=True)
    out_path.mkdir(parents=True, exist_ok=True)

    generate_task(
        TaskConfig(
            task_id=task_id,
            out_dir=out_path,
            data_dir=Path(data_dir).expanduser().resolve(),
            server=server,
            difficulty=TaskDifficulty(difficulty),
            mask_map_path=_resolve_mask_map_path(),
        )
    )
    return out_path


class CyberGymRuntimeHook(BenchmarkRuntimeHook):
    """Attach CyberGym runtime metadata to a benchmark task."""

    def prepare(
        self, *, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec
    ) -> PreparedBenchmarkTask:
        _ = experiment_spec
        environment = dict(run_spec.environment or {})
        metadata = {
            "server": environment.get("server"),
            "data_dir": environment.get("data_dir"),
            "workspace": environment.get("workspace"),
            "difficulty": task.inputs.get("difficulty") or task.metadata.get("split"),
        }
        return PreparedBenchmarkTask(task=task, runtime_metadata=metadata)
