# CyberGym Checkpoint/Resume Design

Status: implemented (2026-11-16)
Owner: QitOS engine + CyberGym benchmark
中文版: [cybergym_checkpoint_resume.zh.md](cybergym_checkpoint_resume.zh.md)

## Goal

Let a CyberGym task that ends at a step boundary — wall-clock budget exhausted,
external kill, OOM, WSL restart — continue from the last completed step on the
next attempt instead of restarting from step 0.

## What exists today

### Wall-clock timeout is cooperative

- `run_cybergym_agent_task` installs `MaxRuntimeCriteria`
  (`qitos/benchmark/cybergym/runner.py`) and sets `RuntimeBudget.max_runtime_seconds`.
- The engine checks the budget at the top of each loop iteration and again in
  `check_stop`. Hitting it produces `stop_reason = "budget_time"` at a step
  boundary. A single LLM or tool call inside a step is never preempted; it runs
  to its own timeout (API 360 s / reasoning 1800 s, shell 60 s).
- External kills (container killed, OOM, host sleep, WSL restart) stop the
  process with no chance to flush anything.

### Checkpoint plumbing is present but unwired

- `Engine(..., checkpoint_store=store)` writes a checkpoint every step, after
  `CHECK_STOP` and `_finalize_step`, with `DurabilityMode.SYNC` by default
  (`qitos/engine/engine.py`).
- `SqliteCheckpointStore` is WAL-backed and resolves `CheckpointConfig(thread_id=…)`
  without a `checkpoint_id` to the newest checkpoint of that thread.
- `Engine.resume_from_checkpoint(config)` exists but has never been used by a
  production path.
- Nothing outside `Engine` and tests passes a `checkpoint_store`; CyberGym runs
  are not checkpointed at all.
- The CyberGym batch `--resume` flag is task-level only: it skips tasks that
  already have a result row. A killed task has no row and restarts at step 0.
  This is documented in `docs/cybergym-local-batch-results.md`.

### Defects in the existing resume path

Found while designing this change; all must be fixed before the path is usable:

1. `resume_from_checkpoint` deserializes with `StateSchema` instead of the
   agent's real state class, so subclass fields (e.g. all of `CyberGymState`)
   fail `StateSchema.from_dict(..., strict=True)`.
2. `_active_task` is taken from `metadata["run_id"]`, so a resumed run would use
   the run id as its task text.
3. `Engine.run` unconditionally replaces `_active_run_id` with the trace run id
   or a random id, so each resume forks a new checkpoint thread and the next
   resume looks at the wrong thread.
4. Checkpoints do not record the state class, and `StepRecord` / trace carry no
   checkpoint id, so resume is not discoverable from an artifact.
5. `resume_from_checkpoint` calls `self.run(task_text)`, dropping the original
   `Task` object and with it the task budget and metadata.

## Design decisions

### D1 — Checkpoint is a step-boundary artifact

A checkpoint is the state after `CHECK_STOP` of step N. A crash inside a step
loses at most that step's partial work; the resumed run repeats no completed
step. Mid-step resume (replaying a half-finished tool call) is explicitly out
of scope.

Checkpoints are written before the loop's `advance_step()`, so the snapshot
still carries `current_step = N`. Resume sets `state.current_step` to
`checkpoint.step + 1` so the resumed loop starts with the same invariant a fresh
run has (`step_id == state.current_step`).

### D2 — Resume restores state, not the conversation

The engine resets memory and history at the start of every `run()`. A resumed
run therefore starts with the restored `state` but an empty chat transcript.
This is acceptable for CyberGym: the agent is state-first (`CyberGymState`
carries candidates, attempts, notes, GDB counters, analysis ids), and the system
prompt is rebuilt from state each step. The limitation is documented; restoring
memory/history is a separate follow-up.

### D3 — Wall-clock budget is per attempt

Each attempt gets the configured `max_runtime_seconds` again. Reusing the
elapsed time would make a resume after `budget_time` an immediate no-op.

Consequences:

- Total wall clock for a task can reach `attempts × max_runtime_seconds`; the
  batch layer owns how many attempts happen.
- The step budget is *not* reset: `current_step` / `max_steps` live in state and
  carry across resumes, so a task cannot exceed its step cap by resuming.

### D4 — Thread identity is stable and tied to the task content

Thread id: `cybergym:<task_id>[:<difficulty>]` — the task id and the difficulty
identify the work. The task package's checksum cannot be part of it: every
attempt regenerates the package with a fresh agent id and therefore a fresh
checksum, so a checksum in the thread id would make every attempt a different
thread and no task could ever resume. The resumed run instead adopts the
current package's identity (agent id, checksum) through the agent's
`refresh_state` hook.

One SQLite database per task: `<checkpoint_dir>/<task_slug>.sqlite`
(e.g. `arvo_1065.sqlite`). Per-task files avoid SQLite write contention when
the batch runs with concurrency > 1.

Default `checkpoint_dir` is `<out_root>/checkpoints`, a sibling of
`workspace/`, so it survives `prepare_task_dir(fresh=True)` (the runner wipes
the task directory on purpose).

### D5 — Resume is opt-in, persistence and resume are separate switches

- `checkpoint_dir` enables writing checkpoints.
- `continue_from_checkpoint` enables reading them at task start.

Both default off: a re-run intended as a clean experiment must not silently
adopt an old state. The batch exposes `--checkpoint-dir` and
`--continue-from-checkpoint`; the latter implies `out_root/checkpoints` when no
directory is given.

### D6 — Only resumable stop reasons are continued

At task start, the latest checkpoint for the thread is inspected:

| previous `state.stop_reason` | action |
| --- | --- |
| absent / `None` | resume (process died before any stop decision) |
| `budget_time` | resume |
| `interrupt` | resume |
| anything else (`final`, `success`, `max_steps`, `stagnation`, …) | discard the thread's checkpoints, start fresh |

`resume_from_checkpoint` clears the stale `stop_reason` before continuing so the
continued run does not inherit a terminal marker. `final_result` is never
cleared.

Discarding the old checkpoints on a fresh start keeps "newest checkpoint of the
thread" unambiguous; completed-run history remains available in the trace.

### D7 — State class travels with the checkpoint

`Checkpoint` gains `state_type` (module-qualified class name). On resume the
engine resolves it as: explicit `state_cls` argument → `checkpoint.state_type`
→ `type(_active_state)` → `StateSchema`. An unimportable class logs a warning
and falls back to `StateSchema`.

The SQLite store gains a `state_type` column and an additive migration
(`ALTER TABLE` guarded by `PRAGMA table_info`) so existing databases keep
working.

### D8 — The resumed run stays on the original checkpoint thread

`Engine.run` accepts an internal `_resume_run_id`; `resume_from_checkpoint`
passes `checkpoint.thread_id`. Each attempt still gets its own trace run id, so
traces remain per-attempt artifacts while checkpoints form one chain.

## API changes

### `qitos.checkpoint`

- `Checkpoint.state_type: Optional[str] = None`, included in `to_dict` /
  `from_dict`, copied by `fork_checkpoint`.
- `SqliteCheckpointStore`: new column `state_type`, read in `get_tuple` / `list`,
  written in `put`.
- No change to the `CheckpointStore` ABC.

### `qitos.engine.Engine`

- `run(..., _resume_run_id: str | None = None)` — internal; keeps the thread id.
- `resume_from_checkpoint(config, *, task=None, state_cls=None)`:
  - restores the state with the resolved class;
  - derives task text from `task` → `state.task` → previous `_active_task`;
  - clears a stale `stop_reason`;
  - continues at `checkpoint.step + 1` on `checkpoint.thread_id`.
- `_save_checkpoint` records `state_type` and keeps the run id stable on resume.
- `run_meta` gains `checkpoint_thread_id`, `last_checkpoint_id`,
  `resumed_from_checkpoint`, `resumed_from_step`.

### `qitos.core.AgentModule.run`

- New keyword `resume_from: CheckpointConfig | None = None`. When set, the same
  engine/spec setup runs first and then `engine.resume_from_checkpoint(...)` is
  called with the original task object and the task kwargs. Typed loosely to
  keep `qitos.core` free of a `qitos.checkpoint` import.
- New hook `refresh_state(state, task, **kwargs)`, called on a restored state
  before the loop continues. The default keeps the state as it is; the CyberGym
  agent uses it to adopt the current package's agent id and checksum.

### CyberGym

- `run_cybergym_agent_task(..., checkpoint_dir=None, continue_from_checkpoint=False)`
  — creates/opens the per-task store, decides resume vs fresh, passes
  `checkpoint_store` through `engine_kwargs` and `resume_from` through
  `agent.run`, closes the store in `finally`, and reports
  `checkpoint_db` / `resumed_from_step` in the returned execution dict.
- `run_cybergym_task` reads `run_spec.environment["checkpoint_dir"]` and
  `["continue_from_checkpoint"]`.
- `run_cybergym_recipe_task` / recipe CLI: `--checkpoint-dir`,
  `--continue-from-checkpoint`.
- `scripts/run_cybergym_batch.py`: both flags, forwarded through
  `run_spec.environment`.

## Runtime flow

```
batch/recipe
  └─ run_cybergym_task(task, run_spec)
      └─ run_cybergym_agent_task(..., checkpoint_dir, continue_from_checkpoint)
          ├─ build Task, agent, env, stop criteria, trace writer
          ├─ open SqliteCheckpointStore(<checkpoint_dir>/<slug>.sqlite)
          ├─ latest = store.list(thread)[0]
          ├─ decide: store.resume_from (resumable) | clear thread + None
          ├─ agent.run(task, resume_from=..., engine_kwargs={checkpoint_store})
          │    └─ Engine.run | Engine.resume_from_checkpoint(task=task)
          └─ finally: store.close()
```

## Failure modes

- **Store cannot be opened** (bad path, permissions, corrupt file): the task
  fails fast with the store error. Optional persistence must not silently
  degrade; a benchmark operator needs to know checkpoints are off.
- **Checkpoint write fails mid-run**: the engine propagates the error and the
  task fails. The previous checkpoint remains resumable.
- **Task package changed**: the checksum in the thread id makes the old
  checkpoints unreachable; a fresh chain starts.
- **Concurrent runs of the same task with the same DB**: unsafe by design.
  Documented; the batch runner schedules each task once.
- **Old checkpoint written before this change** (no `state_type`): falls back
  to `type(_active_state)` / `StateSchema`; with a subclass state this fails
  loudly rather than silently dropping fields.

## Observability

- Trace summary `run_meta`: `checkpoint_thread_id`, `last_checkpoint_id`,
  `resumed_from_checkpoint`, `resumed_from_step`.
- Runner execution dict (embedded in benchmark metadata): `checkpoint_db`,
  `resumed_from_step`.
- A resumed attempt is a normal trace with a normal `run_id`; the link back to
  the previous attempt is `resumed_from_checkpoint`.

## Testing

1. `tests/test_checkpoint_store.py`: `state_type` round-trip through both
   stores, plus the SQLite `ALTER TABLE` migration path.
2. `tests/engine/test_checkpoint_resume.py`: a counting agent with a subclass
   state; run two steps, resume, assert:
   - continuation starts at `checkpoint.step + 1`;
   - subclass fields survive;
   - task text is the original task, not the run id;
   - thread id stays stable across the resumed run;
   - stale `stop_reason` is cleared and `final_result` is preserved.
3. `tests/test_benchmark_cybergym_recipe.py`: mocked agent; assert the store is
   created, `resume_from` is `None` on a fresh task, and a seeded resumable
   checkpoint produces a `resume_from` config; non-resumable checkpoint clears
   the thread.
4. Validation: `pytest -q` plus `flake8` / `mypy` on `qitos/core`, `qitos/engine`.

## Docs and release surface

- `CHANGELOG.md`: Added — CyberGym checkpoint/resume.
- `docs/cybergym-local-batch-results.md`: replace the "no checkpoint/resume"
  known limit with the new behavior and its caveats.
- CyberGym batch usage docs / recipe help text.
- `README.md` news entry.

## Out of scope / follow-ups

- Mid-step resume or idempotent replay of in-flight tool calls.
- Restoring memory/history from a checkpoint.
- Async engine parity (`AsyncEngine` still has no checkpoint support).
- Automatic retry inside one process after `budget_time` (policy belongs to the
  batch layer).
