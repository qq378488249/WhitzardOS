# CyberGym Batch `--resume` Retrying Timed-Out Tasks

Status: implemented (2026-11-17)
Owner: QitOS benchmark + CyberGym deployment
Chinese: [cybergym_resume_retry_timeout.zh.md](cybergym_resume_retry_timeout.zh.md)

## Context

CyberGym batches run with `scripts/run_cybergym_batch.py --resume`, and the
deployment launcher `launch_v14_gdb_149.sh` always passes `--resume`. The
`done` set in `execute_example_jobs` is built from every row in
`OUTPUT_JSONL` without looking at `success` or `stop_reason`, so a task that
stopped at `budget_time` is treated as finished and never re-queued.

Checkpoint continuation already supports `budget_time` / `interrupt`
(`RESUMABLE_STOP_REASONS`), but it only runs when the task is scheduled again.
Re-scheduling is the missing piece.

## Goals

- With an explicit opt-in, `--resume` re-queues tasks whose previous attempt
  ended at `budget_time` / `interrupt`.
- Bound the number of attempts so a task that keeps timing out cannot consume
  budget forever.
- Keep exactly one row per task in the results and report files, so summaries
  and `report.sh` do not double count.
- Leave the default behavior unchanged: cybench / gaia / tau_bench share the
  same helper and see no difference unless they pass the new parameters.

## Non-goals

- Do not change the in-run LLM retry policy (401 / 5xx keep the existing
  recovery behavior).
- Do not split retry eligibility by failure cause (for example "only retry
  timeouts caused by 401").
- Do not change checkpoint write or restore semantics.

## Design

### D1 — Retry eligibility is an explicit stop-reason set

`execute_example_jobs` gains two keyword arguments:

```python
retry_stop_reasons: Optional[Iterable[str]] = None,
max_attempts: int = 3,
```

- They only take effect when `resume=True`.
- A row is re-queued when
  `str(row.stop_reason) in retry_stop_reasons and attempts(row) < max_attempts`.
  A `success=True` row is never retried (CyberGym success rows stop with
  `success`, which is not in the set).
- `attempts` lives in `metadata["attempts"]` and records completed attempts for
  the task. Rows written before this change are treated as one attempt.
- A retried row is written with `metadata["attempts"] = previous + 1`; a first
  attempt writes `1`.

CyberGym passes the `RESUMABLE_STOP_REASONS` constant from
`qitos/benchmark/cybergym/runner.py`. The constant becomes public instead of
keeping a second copy, so checkpoint continuation and batch retry share one
definition.

### D2 — Deduplicate results, keep the newest row

The writer appends each finished task immediately (write + flush + `fsync`),
which is required for crash safety and stays as-is. Retrying produces more than
one row for a task, so:

- At the end of the run, deduplicate by `job_key` (falling back to `task_id`),
  keep the **last** occurrence, and atomically rewrite `OUTPUT_JSONL` through a
  temporary file plus `os.replace`.
- Rewrite `REPORT_JSONL` the same way, keeping the documented one-line-per-task
  contract.
- If the process crashes before the rewrite, the file temporarily holds both
  the old and the new row; the next run still resolves to the newest row and
  converges on one line when it finishes.
- Deduplication only happens when `retry_stop_reasons` is provided; without it
  the existing append semantics are unchanged.

The dedup key matches the existing `done` key: `metadata["job_key"]`, falling
back to `task_id`.

### D3 — CLI flags

`scripts/run_cybergym_batch.py` adds:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--retry-timeout` | off | With `--resume`, re-queue tasks whose last stop was `budget_time` / `interrupt` |
| `--max-attempts` | `3` | Maximum completed attempts per task when retrying |

`--retry-timeout` is a boolean, not a duration threshold: `stop_reason` already
states the timeout precisely, and `elapsed_seconds` sits at the budget ceiling
for nearly every timed-out task. Cost is controlled with `--max-attempts`.

### D4 — Deployment

`launch_v14_gdb_149.sh` gains:

- `RETRY_TIMEOUT`, default `1`. The launcher already passes `--resume`
  unconditionally, so retrying timed-out tasks matches what operators expect
  from a restart; set `0` to restore the old skip-everything behavior.
- `MAX_ATTEMPTS`, default `3`.

Both are written to the runtime config file and forwarded to the batch script.
`.env.compose.example` documents them (the former systemd template was removed when the deployment was consolidated on Compose).

### D5 — Relationship with checkpoint continuation

Retrying only decides whether a task is scheduled again. Whether it continues
from the last step is still controlled by `CONTINUE_FROM_CHECKPOINT`:

- `CONTINUE_FROM_CHECKPOINT=1` with a last stop at `budget_time` / `interrupt`:
  continue from the last completed step.
- `CONTINUE_FROM_CHECKPOINT=0`: start a fresh attempt (the checkpoint chain is
  cleared).

## Behavior matrix

| Row state | Retry off | Retry on |
| --- | --- | --- |
| Successful | skip | skip |
| `budget_time`, attempts < cap | skip | re-run (attempts + 1) |
| `budget_time`, attempts >= cap | skip | skip |
| `interrupt`, attempts < cap | skip | re-run |
| Other failure reason | skip | skip |
| Task absent from the file | run | run |

## Risks and trade-offs

- Repeated timeouts burn budget: bounded by `max_attempts`, after which the task
  is skipped again.
- The dedup rewrite is a full-file write: it only happens when retrying is on,
  uses atomic replacement, and carries the same risk as the existing end-of-run
  write.
- Old rows without `attempts`: counted as one, and the counter is filled in
  after the first retry.
- Report history is folded: the traces directory still keeps every attempt; the
  compact report is for live observation only.

## Verification

- `pytest -q tests/test_benchmark_incremental_results.py`
- New cases cover: timed-out retry, successful rows skipped, attempt cap,
  duplicate convergence, report folding, and unchanged behavior when retrying
  is off.
- Manual: seed a `budget_time` row in a test results file, run with `--resume
  --retry-timeout`, and confirm the task is called again, the file has one row,
  and `metadata.attempts == 2`.
