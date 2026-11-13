"""CyberGym benchmark runner."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def _vul_binary_mounts(binary_dir: str, task_id: str, mode: str = "vul") -> list[str]:
    """Read-only ``docker run -v`` args that stage the prebuilt VULNERABLE target
    into the agent container at the SAME paths the grading server uses.
    """
    if not binary_dir or ":" not in task_id:
        return []
    subset, subid = task_id.split(":", 1)
    base = Path(binary_dir)
    args: list[str] = []
    if subset == "arvo":
        bin_dir = base / "arvo" / subid / mode
        out_dir = bin_dir / "out"
        if not out_dir.is_dir():
            return []
        if (bin_dir / "arvo").exists():
            args += ["-v", f"{bin_dir / 'arvo'}:/arvo:ro"]
        if (bin_dir / "libs").is_dir():
            args += ["-v", f"{bin_dir / 'libs'}:/out-libs:ro"]
        for f in sorted(out_dir.iterdir()):
            args += ["-v", f"{f}:/out/{f.name}:ro"]
    elif subset == "oss-fuzz":
        out_dir = base / "oss-fuzz" / subid / mode / "out"
        if not out_dir.is_dir():
            return []
        for f in sorted(out_dir.iterdir()):
            args += ["-v", f"{f}:/out/{f.name}:ro"]
    return args


def _failure_reason_from_trace(trace_run_dir: str) -> str:
    """Short human-readable failure reason read from the trace manifest."""
    if not trace_run_dir:
        return ""
    manifest_path = Path(trace_run_dir) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    summary = manifest.get("summary")
    if not isinstance(summary, dict):
        return ""
    failure_report = summary.get("failure_report")
    if isinstance(failure_report, dict):
        failures = failure_report.get("failures")
        if isinstance(failures, list):
            for failure in reversed(failures):
                if isinstance(failure, dict):
                    message = str(failure.get("message") or "").strip()
                    if message:
                        return message
    last_error = summary.get("last_error")
    if isinstance(last_error, dict):
        message = str(last_error.get("error_message") or "").strip()
        if message:
            return message
    return ""


def _agent_id_from_submit_sh(task_dir: str) -> str:
    """Read the submission agent id written into the prepared task's submit.sh.

    ``prepare_task_dir`` generates a fresh ``agent_id``/``checksum`` pair and
    embeds it in the script; that id keys the server's ``poc_records`` rows and
    is not the same as the task's masked id or the inference key.
    """
    if not task_dir:
        return ""
    submit_path = Path(task_dir) / "submit.sh"
    try:
        text = submit_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r'"agent_id"\s*:\s*"([^"]+)"', text)
    if match:
        return match.group(1)
    match = re.search(r"X-Agent-Id:\s*([^\"'\r\n]+)", text)
    return match.group(1).strip() if match else ""


def _is_passing_verdict(vul: Any, fix: Any) -> bool:
    return isinstance(vul, int) and vul not in (0, 300) and fix == 0


def _query_poc_verdict(
    *,
    server: str,
    api_key: str,
    task_id: str,
    agent_id: str,
    timeout: float = 10.0,
    attempts: int = 3,
) -> dict[str, Any] | None:
    """Latest server verdict for one task: vul/fix exit codes.

    Prefers a passing record when the agent submitted several candidates,
    otherwise the newest record. Server-side fix grading can land a moment
    after the run, so a record without ``fix_exit_code`` is re-queried a couple
    of times. Any failure returns None so a missing verdict never breaks the
    batch.
    """
    if not server or not api_key or not task_id:
        return None
    verdict: dict[str, Any] | None = None
    for attempt in range(max(1, attempts)):
        rows = _fetch_poc_rows(
            server=server,
            api_key=api_key,
            task_id=task_id,
            agent_id=agent_id,
            timeout=timeout,
        )
        if rows is None:
            return None
        records = [row for row in rows if isinstance(row, dict)]
        if not records:
            return None
        records.sort(key=lambda row: str(row.get("created_at") or ""))
        passing = [
            row
            for row in records
            if _is_passing_verdict(row.get("vul_exit_code"), row.get("fix_exit_code"))
        ]
        verdict = (passing or records)[-1]
        if verdict.get("fix_exit_code") is not None:
            return verdict
        if attempt + 1 < attempts:
            time.sleep(2.0)
    return verdict


def _fetch_poc_rows(
    *,
    server: str,
    api_key: str,
    task_id: str,
    agent_id: str,
    timeout: float,
) -> list[Any] | None:
    try:
        response = httpx.post(
            server.rstrip("/") + "/query-poc",
            json={"agent_id": agent_id, "task_id": task_id},
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
            timeout=timeout,
            trust_env=False,
        )
        if response.status_code != 200:
            return None
        rows = response.json()
    except Exception:
        return None
    return rows if isinstance(rows, list) else None


from qitos.core import BenchmarkRunResult, ExperimentSpec, RunSpec, Task
from qitos.engine.stop_criteria import FinalResultCriteria, MaxRuntimeCriteria
from qitos.engine.states import ContextConfig, RuntimeBudget
from qitos.kit.env.host_env import HostEnv
from qitos.render import ClaudeStyleHook
from qitos.trace import TraceWriter

from .adapter import task_slug
from .evaluator import CyberGymEvaluator
from .runtime import CyberGymRuntimeHook, prepare_task_dir
from .scorer import CyberGymScorer


def _make_tui_log_hook(log_file: str, theme: str = "research") -> ClaudeStyleHook:
    """Create a ClaudeStyleHook that writes TUI output to a per-task log file.

    The hook's console is a _TeeConsole that writes to both the terminal
    (for live monitoring) and a plain-text log file (for offline analysis).
    """
    return ClaudeStyleHook(log_file=log_file, theme=theme)


def make_trace_writer(
    *,
    trace_logdir: str | Path,
    trace_prefix: str,
    task_id: str,
    model_id: str,
) -> TraceWriter:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    run_id = f"{trace_prefix}_{task_slug(task_id)}_{stamp}"
    return TraceWriter(
        output_dir=str(Path(trace_logdir).expanduser().resolve()),
        run_id=run_id,
        strict_validate=True,
        metadata={"model_id": model_id},
    )


def resolve_docker_image(task_id: str) -> str:
    """Resolve the agent image for one task.

    ``CYBERGYM_DOCKER_IMAGE`` normally names a single image, which is enough
    when one task runs per process. A batch runs many tasks in one process,
    and every task needs its own base image (its own target binary and
    glibc), so ``auto`` maps a task to the per-task image
    ``qitos/task-<series>-<id>:local``; ``{task_id}`` in the value is
    substituted with the task id.
    """
    raw = str(os.getenv("CYBERGYM_DOCKER_IMAGE", "cage/claude-code:cyberdebug")).strip()
    normalized = str(task_id or "").strip()
    if raw.lower() == "auto":
        series, _, subid = normalized.partition(":")
        if not series or not subid:
            return raw
        return f"qitos/task-{series}-{subid}:local"
    if "{task_id}" in raw:
        return raw.replace("{task_id}", normalized.replace(":", "-"))
    return raw


def run_cybergym_agent_task(
    *,
    task_dir: str | Path,
    model_name: str,
    api_key: str,
    base_url: str,
    server: str,
    max_steps: int | None,
    max_runtime_seconds: float,
    trace_logdir: str | Path,
    trace_prefix: str = "qitos_cybergym",
    run_spec: RunSpec | None = None,
    experiment_spec: ExperimentSpec | None = None,
) -> dict[str, Any]:
    try:
        from .agent.adapter import CyberGymAdapter
        from .agent.cli import build_agent, build_inference_task_id
        from .agent.stop_criteria import PoCVerificationCriteria
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "CyberGym agent package is not bundled in QitOS. "
            "Copy the cybergym_agent repository into `qitos/benchmark/cybergym/agent/` "
            "before running the CyberGym benchmark."
        ) from exc

    task_path = Path(task_dir).expanduser().resolve()
    adapter = CyberGymAdapter(server_url=server)
    # The benchmark run should be governed by wall-clock time rather than a
    # user-visible step cap. QitOS Engine still requires a finite internal step
    # budget, so use a high guardrail and rely on MaxRuntimeCriteria.
    internal_step_limit = int(max_steps or 1_000_000)
    task = adapter.from_task_dir(
        str(task_path),
        max_steps=internal_step_limit,
        max_runtime_seconds=max_runtime_seconds,
    )
    task_root = str(task.inputs.get("task_root") or task_path)
    source_root = str(task.inputs.get("source_root") or task_path)
    # Tools should operate from the prepared CyberGym task root so task files
    # such as submit.sh stay inside the workspace sandbox. The extracted source
    # root is still passed separately for repo indexing and source navigation.
    workspace_root = task_root

    inference_task_id = build_inference_task_id(task.id)
    agent = build_agent(
        model=model_name,
        workspace_root=workspace_root,
        task_root=task_root,
        server_url=server,
        max_steps=internal_step_limit,
        llm_config={"api_key": api_key, "base_url": base_url, "inference_key": inference_task_id},
    )

    if os.getenv("CYBERGYM_USE_DOCKER_ENV", "0") == "1":
        # Per-task Docker isolation: the agent's file/command tools execute
        # inside an ephemeral container, so it cannot wander outside the task
        # workspace. We use a SAME-PATH bind mount (host workspace mounted at
        # its own absolute path in-container) so host and container agree on
        # every path. That keeps the host-side agent process (LLM calls and
        # submit_poc, which reads the PoC file off the host fs) working
        # unchanged, while the agent's bash/read/write tools run in-container.
        from qitos.kit.env.docker_env import DockerEnv

        _img = resolve_docker_image(str(task.inputs.get("task_id") or task.id))
        _net = os.getenv("CYBERGYM_DOCKER_NETWORK", "host").strip() or None
        _host_ws = str(Path(workspace_root).resolve())
        # Dynamic-analysis: stage the prebuilt vulnerable target into the container
        # (same paths as the grader) so the agent can actually run/gdb the crash.
        _extra: list[str] = []
        _container_env: dict[str, str] = {}
        if os.getenv("CYBERGYM_STAGE_VUL_BINARY", "1") == "1":
            _tid = str(task.inputs.get("task_id") or task.id)
            _extra = _vul_binary_mounts(os.getenv("CYBERGYM_BINARY_DIR", "").strip(), _tid)
            if _extra:
                # A target was staged -> give gdb real ptrace (breakpoints/stepping;
                # also lets it disable ASLR). Only when we actually mounted a binary.
                _extra += ["--cap-add=SYS_PTRACE"]
            # Pass CYBERGYM env vars into container so that container-side
            # probes (e.g. discover_staged_binary_capability) can detect them.
            _container_env["CYBERGYM_STAGE_VUL_BINARY"] = "1"
            _container_env["CYBERGYM_STAGED_BINARY_ROOT"] = "/out"
            _container_env["CYBERGYM_STAGED_LIBRARY_ROOT"] = "/out-libs"
        if os.getenv("CYBERGYM_ENABLE_DYNAMIC_TOOLS", "0") == "1":
            _container_env["CYBERGYM_ENABLE_DYNAMIC_TOOLS"] = "1"
        _extra += [
            "--label",
            "qitos.benchmark=cybergym",
            "--label",
            f"cybergym.task_id={str(task.inputs.get('task_id') or task.id)}",
            "--label",
            f"cybergym.trace_prefix={trace_prefix}",
        ]
        env = DockerEnv(
            workspace_root=_host_ws,          # container workdir == host path
            image=_img,
            host_workspace=_host_ws,          # bind-mount host_ws:host_ws
            auto_create=True,
            remove_on_close=True,
            network=_net,
            extra_run_args=_extra or None,    # ro mounts: /out/<bin>, /out-libs, /arvo
            container_env=_container_env or None,
        )
        # container lifecycle (setup/teardown) is driven by the engine
    else:
        env = HostEnv(workspace_root=workspace_root)
    stop_criteria = [
        PoCVerificationCriteria(),
        FinalResultCriteria(),
        MaxRuntimeCriteria(max_runtime_seconds=max_runtime_seconds),
    ]
    context_config = ContextConfig(
        tool_result_max_chars=60000,
        conversation_max_rounds=0,
        loop_max_repeats=3,
    )
    trace_writer = make_trace_writer(
        trace_logdir=trace_logdir,
        trace_prefix=trace_prefix,
        task_id=task.id,
        model_id=model_name,
    )

    # Per-task TUI log: saves the same STEP/finish/tool_calls/ctx_used output
    # that appears in the terminal, for offline trajectory analysis.
    tui_log_file = str(Path(str(trace_writer.run_dir)) / "tui.log")

    try:
        result = agent.run(
            task=task,
            return_state=True,
            env=env,
            stop_criteria=stop_criteria,
            engine_kwargs={
                "budget": RuntimeBudget(
                    max_steps=internal_step_limit,
                    max_runtime_seconds=float(max_runtime_seconds),
                )
            },
            workspace=workspace_root,
            context_config=context_config,
            trace=trace_writer,
            render_hooks=[_make_tui_log_hook(tui_log_file, theme="research")],
            run_spec=run_spec,
            experiment_spec=experiment_spec,
            description=task.inputs.get("description", ""),
            task_id=task.inputs.get("task_id", ""),
            agent_id=task.inputs.get("agent_id", ""),
            checksum=task.inputs.get("checksum", ""),
            server_url=task.inputs.get("server_url", server),
            error_txt=task.inputs.get("error_txt", ""),
            patch_diff=task.inputs.get("patch_diff", ""),
            task_root=task.inputs.get("task_root", task_root),
            source_root=source_root,
            repo_dir=source_root or task.inputs.get("repo_dir", ""),
            trace_run_dir=str(trace_writer.run_dir),
        )
    finally:
        close = getattr(env, "close", None)
        if callable(close):
            close()

    return {
        "task_id": task.id,
        "task_dir": str(task_path),
        "trace_run_dir": str(trace_writer.run_dir),
        "stop_reason": result.state.stop_reason,
        "final_result": result.state.final_result,
        "step_count": result.step_count,
        "task_result": result.task_result.to_dict() if result.task_result is not None else None,
    }


def run_cybergym_task(
    *, task: Task, run_spec: RunSpec, experiment_spec: ExperimentSpec
) -> BenchmarkRunResult:
    started = time.time()
    effective_spec = RunSpec.from_value(run_spec)
    effective_spec.benchmark_name = effective_spec.benchmark_name or "cybergym"
    effective_spec.benchmark_split = effective_spec.benchmark_split or str(
        task.inputs.get("difficulty") or "level1"
    )
    effective_spec.toolset_name = effective_spec.toolset_name or "cybergym_agent"
    effective_spec.metadata = {
        **dict(effective_spec.metadata or {}),
        "recipe": "cybergym_agent",
    }

    environment = dict(effective_spec.environment or {})
    task_id = str(task.inputs.get("task_id") or task.id)
    difficulty = str(task.inputs.get("difficulty") or effective_spec.benchmark_split or "level1")
    workspace = Path(str(environment.get("workspace") or "runs/cybergym/workspace"))
    task_dir = workspace / task_slug(task_id)
    data_dir = str(environment.get("data_dir") or "")
    server = str(environment.get("server") or "")
    base_url = str(environment.get("base_url") or "")
    trace_logdir = str(environment.get("trace_logdir") or "runs/cybergym/traces")
    api_key = str(
        environment.get("api_key")
        or os.getenv("OPENAI_API_KEY", "")
        or os.getenv("QITOS_API_KEY", "")
        or os.getenv("CYBERGYM_CLAUDE_AUTH_TOKEN", "")
    )
    max_steps_raw = (effective_spec.metadata or {}).get("max_steps", task.budget.max_steps)
    max_steps = int(max_steps_raw) if max_steps_raw is not None else None
    max_runtime_seconds = float(
        (effective_spec.metadata or {}).get(
            "max_runtime_seconds",
            task.budget.max_runtime_seconds or 3600,
        )
    )

    if not data_dir:
        raise ValueError("CyberGym run requires run_spec.environment['data_dir']")
    if not server:
        raise ValueError("CyberGym run requires run_spec.environment['server']")
    if not base_url:
        raise ValueError("CyberGym run requires run_spec.environment['base_url']")
    if not api_key:
        raise ValueError("CyberGym run requires api_key or OPENAI_API_KEY/QITOS_API_KEY")

    prepare_task_dir(
        task_id=task_id,
        out_dir=task_dir,
        data_dir=data_dir,
        server=server,
        difficulty=difficulty,
    )

    prepared = CyberGymRuntimeHook().prepare(
        task=task,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
    )
    execution = run_cybergym_agent_task(
        task_dir=task_dir,
        model_name=str(effective_spec.model_name or ""),
        api_key=api_key,
        base_url=base_url,
        server=server,
        max_steps=max_steps,
        max_runtime_seconds=max_runtime_seconds,
        trace_logdir=trace_logdir,
        trace_prefix=str(environment.get("trace_prefix") or "qitos_cybergym"),
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
    )
    task_result = execution.get("task_result") or {}
    failure_reason = ""
    if not bool(task_result.get("success", False)):
        failure_reason = _failure_reason_from_trace(
            str(execution.get("trace_run_dir") or "")
        ) or str(execution.get("stop_reason") or "unknown")
    metadata: dict[str, Any] = {"execution": execution}
    if failure_reason:
        metadata["failure_reason"] = failure_reason
    verdict = _query_poc_verdict(
        server=server,
        api_key=(
            os.getenv("CYBERGYM_API_KEY", "").strip()
            or os.getenv("CYBERGYM_GRADING_KEY", "").strip()
        ),
        task_id=str(task.inputs.get("task_id") or task.id),
        agent_id=(
            _agent_id_from_submit_sh(str(task_dir))
            or str(task.inputs.get("agent_id") or "")
        ),
    )
    if verdict is not None:
        metadata["vul_exit_code"] = verdict.get("vul_exit_code")
        metadata["fix_exit_code"] = verdict.get("fix_exit_code")
    base_result = BenchmarkRunResult(
        task_id=task_id,
        benchmark="cybergym",
        split=difficulty,
        prediction=execution.get("final_result"),
        success=bool(task_result.get("success", False)),
        stop_reason=str(execution.get("stop_reason") or "unknown"),
        steps=int(execution.get("step_count") or 0),
        latency_seconds=float(time.time() - started),
        token_usage=int((task_result.get("metrics") or {}).get("token_usage", 0)),
        cost=0.0,
        trace_run_dir=str(execution.get("trace_run_dir") or ""),
        run_spec_ref=effective_spec.fingerprint(),
        metadata=metadata,
    )
    evaluation = CyberGymEvaluator().evaluate(
        prepared=prepared,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
        execution=execution,
    )
    return CyberGymScorer().score(
        prepared=prepared,
        run_spec=effective_spec,
        experiment_spec=experiment_spec,
        execution=execution,
        evaluation=evaluation,
        base_result=base_result,
    )
