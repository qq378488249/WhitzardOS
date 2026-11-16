"""Engine checkpoint/resume integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from qitos import Action, AgentModule, Decision, Engine, StateSchema, ToolRegistry, tool
from qitos.checkpoint import CheckpointConfig, SqliteCheckpointStore
from qitos.engine.states import RuntimeBudget


@dataclass
class _ResumeState(StateSchema):
    marks: List[str] = field(default_factory=list)


class _ResumeAgent(AgentModule[_ResumeState, dict[str, Any], Action]):
    name = "resume_agent"

    def __init__(self, steps_before_final: int = 1):
        registry = ToolRegistry()

        @tool(name="mark")
        def mark(value: str) -> str:
            return value

        registry.register(mark)
        self.steps_before_final = steps_before_final
        self.seen_stop_reasons: List[Optional[str]] = []
        super().__init__(tool_registry=registry)

    def init_state(self, task: str, **kwargs: Any) -> _ResumeState:
        return _ResumeState(task=task, max_steps=int(kwargs.get("max_steps", 10)))

    def decide(
        self, state: _ResumeState, observation: dict[str, Any]
    ) -> Decision[Action]:
        self.seen_stop_reasons.append(state.stop_reason)
        if state.current_step < self.steps_before_final:
            return Decision.act(
                actions=[Action(name="mark", args={"value": "x"})],
                rationale="mark",
            )
        return Decision.final("done")

    def reduce(
        self,
        state: _ResumeState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> _ResumeState:
        state.marks.append(f"step-{state.current_step}")
        return state


def _engine(store, agent: _ResumeAgent, max_steps: int = 10) -> Engine:
    return Engine(agent, budget=RuntimeBudget(max_steps=max_steps), checkpoint_store=store)


def _latest(store, thread_id: str):
    return next(store.list(CheckpointConfig(thread_id=thread_id), limit=1), None)


def test_run_records_state_type_and_thread(tmp_path):
    store = SqliteCheckpointStore(str(tmp_path / "cp.sqlite"))
    try:
        result = _engine(store, _ResumeAgent(steps_before_final=1)).run("resume task")

        latest = _latest(store, result.run_id)
        assert latest is not None
        assert latest.checkpoint.state_type == (
            f"{_ResumeState.__module__}._ResumeState"
        )
        assert latest.checkpoint.thread_id == result.run_id
        assert latest.checkpoint.step == result.records[-1].step_id
    finally:
        store.close()


def test_resume_continues_from_next_step_and_restores_subclass_state(tmp_path):
    store = SqliteCheckpointStore(str(tmp_path / "cp.sqlite"))
    try:
        first = _engine(store, _ResumeAgent(steps_before_final=1)).run("resume task")
        latest = _latest(store, first.run_id)
        assert latest is not None
        resume_step = latest.checkpoint.step

        agent = _ResumeAgent(steps_before_final=resume_step + 1)
        resumed = _engine(store, agent).resume_from_checkpoint(
            CheckpointConfig(thread_id=first.run_id)
        )

        # Continues at the step after the checkpoint, on the same thread.
        assert resumed.records[0].step_id == resume_step + 1
        assert resumed.run_id == first.run_id
        assert all(
            rec.step_id > resume_step for rec in resumed.records
        )

        # Subclass state survived, including work done before the resume.
        assert isinstance(resumed.state, _ResumeState)
        assert resumed.state.marks[: resume_step + 1] == [
            f"step-{i}" for i in range(resume_step + 1)
        ]
        assert resumed.state.task == "resume task"

        # The previous terminal stop reason does not leak into the new run.
        assert agent.seen_stop_reasons[0] is None

        # New checkpoints keep accumulating on the original thread.
        new_latest = _latest(store, first.run_id)
        assert new_latest is not None
        assert new_latest.checkpoint.step > resume_step
        assert new_latest.checkpoint.state_type == (
            f"{_ResumeState.__module__}._ResumeState"
        )
    finally:
        store.close()


def test_agent_run_accepts_resume_from(tmp_path):
    """The production path: AgentModule.run(..., resume_from=config)."""
    store = SqliteCheckpointStore(str(tmp_path / "cp.sqlite"))
    try:
        first = _ResumeAgent(steps_before_final=1).run(
            "resume task",
            return_state=True,
            engine_kwargs={
                "checkpoint_store": store,
                "budget": RuntimeBudget(max_steps=10),
            },
        )
        latest = _latest(store, first.run_id)
        assert latest is not None

        resumed = _ResumeAgent(steps_before_final=latest.checkpoint.step + 1).run(
            "resume task",
            return_state=True,
            engine_kwargs={
                "checkpoint_store": store,
                "budget": RuntimeBudget(max_steps=10),
            },
            resume_from=CheckpointConfig(thread_id=first.run_id),
        )

        assert resumed.records[0].step_id == latest.checkpoint.step + 1
        assert isinstance(resumed.state, _ResumeState)
        assert resumed.state.marks[: latest.checkpoint.step + 1] == [
            f"step-{i}" for i in range(latest.checkpoint.step + 1)
        ]
    finally:
        store.close()


def test_resume_preserves_explicit_task_object(tmp_path):
    store = SqliteCheckpointStore(str(tmp_path / "cp.sqlite"))
    try:
        first = _engine(store, _ResumeAgent(steps_before_final=1)).run("resume task")
        latest = _latest(store, first.run_id)
        assert latest is not None

        agent = _ResumeAgent(steps_before_final=latest.checkpoint.step + 1)
        resumed = _engine(store, agent).resume_from_checkpoint(
            CheckpointConfig(thread_id=first.run_id),
            task="explicit task",
        )
        assert resumed.state.task == "explicit task"
    finally:
        store.close()
