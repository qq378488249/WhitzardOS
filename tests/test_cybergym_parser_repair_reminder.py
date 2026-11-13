from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from qitos.benchmark.cybergym.agent.agent import CyberGymAgent
from qitos.benchmark.cybergym.agent.agent_impl.core.constants import (
    PARSER_REPAIR_REMINDER_TEXT,
)
from qitos.benchmark.cybergym.agent.agent_impl.observation.validation import (
    ValidationMixin,
)
from qitos.benchmark.cybergym.agent.state import CyberGymState


def test_parser_error_schedules_one_shot_repair_reminder() -> None:
    state = CyberGymState(task="demo task")
    decision = SimpleNamespace(meta={"parser_error": True})

    ValidationMixin._maybe_set_parser_repair_reminder(state, decision)

    assert state.pending_reminder == PARSER_REPAIR_REMINDER_TEXT


def test_parser_repair_reminder_does_not_clobber_existing_reminder() -> None:
    state = CyberGymState(task="demo task")
    state.pending_reminder = "existing reminder"
    decision = SimpleNamespace(meta={"parser_error": True})

    ValidationMixin._maybe_set_parser_repair_reminder(state, decision)

    assert state.pending_reminder == "existing reminder"


def test_productive_decision_leaves_reminder_empty() -> None:
    state = CyberGymState(task="demo task")

    ValidationMixin._maybe_set_parser_repair_reminder(
        state, SimpleNamespace(meta={"decision_source": "native_tool_calls"})
    )
    ValidationMixin._maybe_set_parser_repair_reminder(
        state, SimpleNamespace(meta=None)
    )

    assert state.pending_reminder == ""


def test_reduce_schedules_reminder_for_parser_error(tmp_path: Path) -> None:
    agent = CyberGymAgent(llm=None, workspace_root=str(tmp_path))
    state = CyberGymState(task="demo task")
    state.current_step = 3
    observation = SimpleNamespace(action_results=[])

    agent.reduce(
        state,
        observation,
        SimpleNamespace(meta={"parser_error": True}),
    )

    assert state.pending_reminder == PARSER_REPAIR_REMINDER_TEXT
