from __future__ import annotations

import pytest

from qitos.core.errors import (
    ErrorCategory,
    LLMCallError,
    StopReason,
    classify_exception,
    extract_status_code,
)
from qitos.engine._model_runtime import _ModelRuntime
from qitos.engine.recovery import RecoveryPolicy


def test_classify_exception_marks_stream_timeout_as_recoverable_model_error() -> None:
    info = classify_exception(RuntimeError("stream timeout"), "DECIDE", 7)

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True
    assert info.phase == "DECIDE"
    assert info.step_id == 7


def test_classify_exception_marks_timed_out_message_as_recoverable_model_error() -> None:
    info = classify_exception(RuntimeError("request timed out while streaming"), "PROPOSE", 3)

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True


def test_recovery_policy_continues_on_stream_timeout() -> None:
    decision = RecoveryPolicy().handle(state=None, phase="DECIDE", step_id=11, exc=RuntimeError("stream timeout"))

    assert decision.handled is True
    assert decision.continue_run is True
    assert decision.stop_reason is None


LITELLM_401 = (
    "Error code: 401 - litellm.AuthenticationError: AuthenticationError: "
    "OpenAIException - Unauthorized. Received Model Group=ap-ds-v4-flash "
    "Available Model Group Fallbacks=None"
)


def test_classify_exception_marks_401_as_recoverable_model_error() -> None:
    info = classify_exception(RuntimeError(LITELLM_401), "DECIDE", 18)

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True
    assert info.details.get("auth_error") is True


def test_recovery_policy_continues_on_401() -> None:
    decision = RecoveryPolicy().handle(
        state=None, phase="DECIDE", step_id=18, exc=RuntimeError(LITELLM_401)
    )

    assert decision.handled is True
    assert decision.continue_run is True
    assert decision.stop_reason is None
    assert decision.backoff_seconds > 0


def test_recovery_policy_stops_after_repeated_401() -> None:
    """A permanently dead key must not spin forever: the consecutive-failure
    cap still applies to recoverable auth errors."""

    policy = RecoveryPolicy(max_recoveries_per_run=3)
    decisions = [
        policy.handle(state=None, phase="DECIDE", step_id=step, exc=RuntimeError(LITELLM_401))
        for step in (10, 11, 12, 13)
    ]

    assert [d.continue_run for d in decisions] == [True, True, True, False]
    assert decisions[-1].stop_reason == StopReason.UNRECOVERABLE_ERROR
    assert decisions[-1].note == "max_recovery_exhausted"


def test_classify_exception_keeps_401_fatal_outside_model_phases() -> None:
    """Auth failures raised outside DECIDE/PROPOSE are not the provider's
    responsibility; keep the default non-recoverable system classification."""

    info = classify_exception(RuntimeError(LITELLM_401), "CHECK_STOP", 18)

    assert info.recoverable is False
    assert info.category == ErrorCategory.SYSTEM


def test_classify_exception_marks_gateway_503_as_recoverable_model_error() -> None:
    """litellm renders gateway outages as 'ServiceUnavailableError', which
    contains no spaced 'service unavailable' phrase; the class name must still
    match so a transient 503 does not kill the run."""

    exc = RuntimeError(
        "Error code: 503 - litellm.ServiceUnavailableError: "
        "ServiceUnavailableError: OpenAIException - upstream connect error"
    )
    info = classify_exception(exc, "DECIDE", 4)

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True


def test_classify_exception_marks_bare_gateway_502_as_recoverable_model_error() -> None:
    info = classify_exception(
        RuntimeError("Error code: 502 - upstream proxy returned an invalid response"),
        "DECIDE",
        5,
    )

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True


def test_recovery_policy_continues_on_gateway_503() -> None:
    decision = RecoveryPolicy().handle(
        state=None,
        phase="DECIDE",
        step_id=6,
        exc=RuntimeError("Error code: 503 - litellm.ServiceUnavailableError"),
    )

    assert decision.continue_run is True
    assert decision.stop_reason is None


class _RaisingLLM:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def call_raw(self, messages, **kwargs):  # noqa: ANN001, ANN003
        raise self._exc


def test_engine_wraps_any_provider_error_as_llm_call_error() -> None:
    runtime = _ModelRuntime(None)
    with pytest.raises(LLMCallError) as excinfo:
        runtime._call_llm(
            _RaisingLLM(RuntimeError("Error code: 400 - invalid model name")),
            [{"role": "user", "content": "hi"}],
            {},
        )

    assert "invalid model name" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_classify_exception_treats_llm_call_error_as_recoverable_model_error() -> None:
    info = classify_exception(
        LLMCallError("Error code: 400 - invalid model name"), "DECIDE", 9
    )

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True
    assert info.details.get("llm_call_error") is True
    assert "auth_error" not in info.details


def test_classify_exception_keeps_auth_detail_on_llm_call_error() -> None:
    info = classify_exception(LLMCallError(LITELLM_401), "DECIDE", 9)

    assert info.recoverable is True
    assert info.details.get("auth_error") is True


def test_recovery_policy_backoff_doubles_per_consecutive_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF", "1")
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF_MAX", "30")
    policy = RecoveryPolicy(max_recoveries_per_run=10)

    delays = [
        policy.handle(
            state=None,
            phase="DECIDE",
            step_id=step,
            exc=RuntimeError("stream timeout"),
        ).backoff_seconds
        for step in range(1, 7)
    ]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_recovery_policy_restarts_backoff_after_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF", "2")
    policy = RecoveryPolicy()

    first = policy.handle(
        state=None, phase="DECIDE", step_id=1, exc=RuntimeError("stream timeout")
    )
    second = policy.handle(
        state=None, phase="DECIDE", step_id=2, exc=RuntimeError("stream timeout")
    )
    # A successful step in between moves step_id by more than one, which
    # resets the consecutive-failure streak.
    after_gap = policy.handle(
        state=None, phase="DECIDE", step_id=9, exc=RuntimeError("stream timeout")
    )

    assert first.backoff_seconds == 2.0
    assert second.backoff_seconds == 4.0
    assert after_gap.backoff_seconds == 2.0


def test_recovery_policy_reads_general_backoff_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF", "3.5")
    policy = RecoveryPolicy()

    decision = policy.handle(
        state=None, phase="DECIDE", step_id=1, exc=RuntimeError("stream timeout")
    )

    assert decision.backoff_seconds == 3.5


def test_recovery_policy_clamps_general_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF", "120")
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF_MAX", "9999")
    policy = RecoveryPolicy()

    assert policy.retry_backoff_seconds == 60.0
    assert policy.retry_backoff_max_seconds == 600.0


def test_recovery_policy_zero_backoff_disables_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QITOS_RECOVERY_BACKOFF", "0")
    policy = RecoveryPolicy()

    decision = policy.handle(
        state=None, phase="DECIDE", step_id=1, exc=RuntimeError("stream timeout")
    )

    assert decision.backoff_seconds == 0.0


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_extract_status_code_reads_attribute_through_wrapping() -> None:
    wrapped = LLMCallError("provider failed")
    wrapped.__cause__ = _StatusError("provider failed", 503)

    assert extract_status_code(wrapped) == 503


def test_extract_status_code_parses_message_prefix() -> None:
    assert extract_status_code(RuntimeError("Error code: 401 - Unauthorized")) == 401
    assert extract_status_code(RuntimeError("plain failure")) is None
