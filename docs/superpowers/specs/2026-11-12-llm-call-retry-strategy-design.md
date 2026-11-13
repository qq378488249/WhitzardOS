# LLM Call Retry Strategy Design

**Date:** 2026-11-13  
**Status:** Implemented (commits `975e931`, `88e9f1e`)  
**Scope:** `qitos.core.errors`, `qitos.engine._model_runtime`, `qitos.engine._control_runtime`, `qitos.engine.recovery`

## 1. Goal

Make long-running QitOS runs resilient to transient and poorly classified model-provider failures without hiding permanently broken configurations.

The runtime should:

- retry provider-call failures by default, including failures whose wording is not recognized by a curated marker list
- keep one bounded recovery policy for all recoverable failures
- apply progressive backoff so short blips recover quickly while sustained outages do not hot-loop
- preserve the dedicated reactive-compaction path for context-overflow failures
- stop deterministically when a failure streak indicates a non-transient problem

## 2. Background

### 2.1 Original failure mode

Before this work, `classify_exception()` relied on provider-specific substrings to decide whether a failure in a model-driven phase was recoverable. That approach had two structural problems:

1. **False negatives.** New gateway formats (for example litellm's `ServiceUnavailableError` class name without a space, or future provider messages) could fall through to the generic `system_error` path and terminate a run as `unrecoverable_error`.
2. **Auth sensitivity.** Mid-run credential rotation or gateway reloads surfaced as HTTP `401` / `Unauthorized` failures and were treated as fatal, killing runs that had been progressing normally.

Commit `975e931` made provider authentication failures recoverable in model-driven phases. Commit `88e9f1e` generalized the strategy: every provider-call exception is wrapped and retried under one bounded, progressive policy.

### 2.2 Constraints

- Preserve the single-kernel architecture: recovery stays inside the existing `Engine` loop and `RecoveryPolicy`; no parallel retry executor.
- Keep stop-reason and trace semantics auditable (`recoverable_continue`, `max_recovery_exhausted`, `context_overflow`, failure diagnostics).
- Do not let a permanently dead key or an endlessly failing endpoint spin forever.
- Keep context overflow as a special case because the correct first response is compaction, not blind delay-and-retry.

## 3. Design overview

```text
LLM request
  -> _ModelRuntime._call_llm / _dispatch_llm_call
  -> provider raises
  -> wrap as LLMCallError   (except ContextOverflowError, which passes through)
  -> _ControlRuntime.recover(...)
       -> on_recover hook + *_ERROR / RECOVER trace events
       -> context overflow? reactive compaction (bounded) else stop as CONTEXT_OVERFLOW
       -> RecoveryPolicy.handle(...)
            -> classify_exception(...)
            -> non-recoverable: stop (UNRECOVERABLE_ERROR / unrecoverable_stop)
            -> recoverable: update consecutive streak, backoff, continue or exhaust
  -> engine retries the step (or stops)
```

The strategy has three cooperating layers:

| Layer | Responsibility |
|---|---|
| `_ModelRuntime` | Normalize provider-call failures at the call boundary |
| `classify_exception` / `LLMCallError` | Assign a recoverable `ErrorCategory` and diagnostic details independent of provider wording |
| `RecoveryPolicy` + `_ControlRuntime.recover` | Bound retries, apply progressive delay, and integrate with context-overflow recovery |

## 4. Call-boundary wrapping

`qitos/engine/_model_runtime.py` routes provider calls through:

- `_dispatch_llm_call(llm, messages, request_options)` — actual call/stream dispatch (including `call_raw`/`__call__` and streaming fallback behavior)
- `_call_llm(llm, messages, request_options)` — boundary wrapper

`_call_llm` behavior:

```python
try:
    return self._dispatch_llm_call(llm, messages, request_options)
except (ContextOverflowError, LLMCallError):
    raise
except Exception as exc:
    raise LLMCallError(str(exc)) from exc
```

`LLMCallError` (`qitos.core.errors`) is a plain exception marker whose `__cause__` preserves the original provider exception. Wrapping at the call boundary means classification does not need to enumerate every gateway/protocol error shape: a failure that came from the provider call is retryable by construction, subject to policy limits.

`ContextOverflowError` is intentionally excluded from wrapping so `_ControlRuntime.recover()` can detect it and attempt reactive compaction before falling back to the normal recovery decision.

## 5. Classification

`classify_exception(exc, phase, step_id)` in `qitos/core/errors.py` produces `RuntimeErrorInfo(category, message, phase, step_id, recoverable, details)`.

### 5.1 `LLMCallError`

Any `LLMCallError` is classified as:

- `category`: `ErrorCategory.MODEL`
- `recoverable`: `True`
- `details`: always includes `{"llm_call_error": True}`; adds `{"auth_error": True}` when the message matches auth markers

Auth markers (case-insensitive substring match): `error code: 401`, `authenticationerror`, `authentication error`, `unauthorized`, `invalid api key`, `invalid_api_key`, `incorrect api key`.

The `auth_error` detail is retained for diagnostics and observability even though auth failures no longer take a separate retry path.

### 5.2 Pre-wrap / non-LLM paths retained for compatibility and specificity

Classification still contains targeted rules used when exceptions reach it without `LLMCallError` wrapping (or for non-call failures):

- **API context overflow** (`context_length_exceeded`, prompt-too-long markers): recoverable `model_error` with `details={"api_context_overflow": True}`.
- **Network/transport errors** in `decide`/`propose` phases: recoverable `model_error`.
- **Auth failures** in model-driven phases (`decide`, `propose`, `plan`, `formulation`, `investigation`): recoverable `model_error` with `auth_error` detail. Auth failures outside these phases remain non-recoverable `system_error`.
- **Transient model/gateway failures** in model-driven phases (timeouts, `no healthy upstream`, `service unavailable`/`serviceunavailable`, `bad gateway`, `gateway timeout`, `temporarily unavailable`, HTTP `502`/`503`/`504`, `overloaded`): recoverable `model_error`.
- **`ACT`-phase exceptions:** recoverable `tool_error` (default).
- **Parse-shaped `ValueError`s:** recoverable `parse_error`.
- **State-shaped `TypeError`/`AttributeError`/`AssertionError`:** non-recoverable `state_error`.
- **Fallback:** non-recoverable `system_error`.

## 6. Recovery policy

`RecoveryPolicy` (`qitos/engine/recovery.py`) owns retry budgeting, streak tracking, diagnostics, and backoff calculation.

### 6.1 Consecutive-failure streak

The policy tracks a **consecutive** recoverable-failure streak, not a lifetime total:

- if the failing `step_id` is contiguous with the previous failure (`step_id - last_step_id <= 1`), increment the streak
- otherwise reset the streak to `1`

A successful step breaks contiguity, so scattered transient blips are tolerated across a long run while a sustained outage exhausts the cap quickly.

### 6.2 Cap and stop behavior

- `max_recoveries_per_run` (env: `QITOS_MAX_RECOVERIES`, default `100`) is the maximum consecutive recoverable failures allowed to continue.
- While `streak <= max_recoveries_per_run`, `handle()` returns `RecoveryDecision(handled=True, continue_run=True, note="recoverable_continue", backoff_seconds=...)` and records a diagnostic with `decision="continue"`.
- When `streak > max_recoveries_per_run`, it returns `continue_run=False`, `stop_reason=StopReason.UNRECOVERABLE_ERROR`, `note="max_recovery_exhausted"`, and records `decision="stop"`.
- Non-recoverable failures return `stop_reason=UNRECOVERABLE_ERROR`, `note="unrecoverable_stop"` immediately.

The cap is applied uniformly (including auth-marked failures) so a permanently invalid key cannot spin indefinitely.

### 6.3 Progressive backoff

Backoff is computed per streak attempt:

```text
delay(streak) = min(base * 2^(streak-1), max_delay)   when base > 0
delay(streak) = 0                                     when base == 0
```

| Knob | Env var | Default | Clamp | Meaning |
|---|---|---:|---:|---|
| Base delay | `QITOS_RECOVERY_BACKOFF` | `1.0` s | `[0, 60]` | Delay before the first retry |
| Max delay | `QITOS_RECOVERY_BACKOFF_MAX` | `30.0` s | `[0, 600]` | Upper bound on progressive delay |
| Streak cap | `QITOS_MAX_RECOVERIES` | `100` | parsed as int, fallback `100` | Max consecutive recoverable failures |

Default delay sequence: `1, 2, 4, 8, 16, 30, 30, ...` seconds.

`RecoveryPolicy` reads env vars at construction time. `RecoveryDecision.backoff_seconds` is the source of truth consumed by the control runtime.

## 7. Control-runtime integration

`_ControlRuntime.recover()` (`qitos/engine/_control_runtime.py`) coordinates observability, overflow handling, and policy execution:

1. Emit `on_recover` hook context and phase-specific error events (`DECIDE_ERROR` / `ACT_ERROR` as applicable) plus `RECOVER`.
2. Call the optional custom `recovery_handler(state, phase, exc)`.
3. **Context overflow fast path:** if the exception is `ContextOverflowError` or matches API overflow markers, attempt reactive history compaction (bounded attempts, requires reactive compaction enabled and sufficient history). On successful compaction, emit a `COMPACT` event (`stage=reactive_compact`) and return `True` to retry without applying backoff policy. If compaction cannot recover, set `StopReason.CONTEXT_OVERFLOW` and return `False`.
4. Delegate to `RecoveryPolicy.handle(...)` for all other failures.
5. Apply `stop_reason`, optional `state_patch` / `instruction_patch`, and sleep for `backoff_seconds` when continuing.
6. Return whether execution should continue.

This ordering ensures overflow gets a semantic recovery attempt first, while all other provider failures share one retry budget and delay curve.

## 8. Observability and diagnostics

- `RecoveryTracker` stores `FailureDiagnostic` entries (`step_id`, `phase`, `category`, `message`, `recoverable`, `decision`, `recommendation`) for every handled failure.
- `build_failure_report(policy, stop_reason)` returns a serializable summary (`failure_count`, `failures`, `stop_reason`).
- Trace/event surfaces reflect recovery attempts through `DECIDE_ERROR`/`ACT_ERROR`/`RECOVER` events, hook dispatch, and stop reasons (`UNRECOVERABLE_ERROR` with notes such as `unrecoverable_stop` / `max_recovery_exhausted`, or `CONTEXT_OVERFLOW`).
- `RuntimeErrorInfo.details` carries machine-readable markers (`llm_call_error`, `auth_error`, `api_context_overflow`) for downstream analysis.

## 9. Configuration

Runtime knobs (environment variables):

```bash
QITOS_MAX_RECOVERIES=100      # consecutive recoverable failures before stop
QITOS_RECOVERY_BACKOFF=1.0    # base retry delay in seconds (0 disables waiting)
QITOS_RECOVERY_BACKOFF_MAX=30 # max progressive retry delay in seconds
```

See `docs/reference/configuration.mdx` for the user-facing reference and clamping rules.

## 10. Non-goals

- **Per-provider retry tuning.** The strategy is intentionally uniform at the engine boundary; provider adapters may still implement their own internal transport retries, but engine recovery semantics do not branch on vendor.
- **Infinite retry for any failure class.** All recoverable failures are capped by `QITOS_MAX_RECOVERIES`.
- **Replacing context management.** Overflow handling remains reactive compaction + eventual `CONTEXT_OVERFLOW`, not generic delayed retry.
- **Changing tool/parse/state error semantics** beyond what existing classification already defines.

## 11. Verification

Primary regression coverage: `tests/test_runtime_recovery.py`.

It validates:

- classification of timeouts, 401/auth failures, gateway 502/503 variants, and generic `LLMCallError` as recoverable model errors (with expected details)
- fatal classification boundaries (e.g. auth outside model phases)
- `RecoveryPolicy` continuation, streak reset after a gap, cap exhaustion (`max_recovery_exhausted`)
- progressive backoff doubling, max clamp, zero-backoff behavior, and env-driven clamping
- `_ModelRuntime._call_llm` wrapping arbitrary provider exceptions as `LLMCallError` while preserving cause

Relevant code/test commands:

```bash
pytest -q tests/test_runtime_recovery.py
pytest -q
flake8 qitos/core qitos/engine qitos/models qitos/trace
mypy qitos/core qitos/engine qitos/models qitos/trace
```

## 12. Future considerations

- Expose retry/backoff settings on `RecoveryPolicy` / runtime config objects (not only env vars) if programmatic per-run control is needed.
- Add structured retry-attempt counters to step/trace records if operators need attempt-level accounting beyond failure diagnostics.
- Revisit default cap/backoff from long-batch telemetry (CyberGym and similar workloads) if failure profiles change.
