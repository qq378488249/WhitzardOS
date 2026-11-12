from __future__ import annotations

from qitos.core.errors import ErrorCategory, StopReason, classify_exception
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
