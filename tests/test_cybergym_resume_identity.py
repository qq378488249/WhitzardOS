"""CyberGym agent: state refresh for resumed runs."""

from __future__ import annotations

from typing import Any

from qitos.benchmark.cybergym.agent.agent_impl.prompt.state_init import StateInitMixin
from qitos.benchmark.cybergym.agent.state_models.core import CyberGymState


class _Agent(StateInitMixin):
    """Minimal host for the mixin; refresh_state only touches the state."""

    max_steps = 30
    workspace_root = ""
    server_url = "http://server"


def test_refresh_state_adopts_the_current_package_identity():
    state = CyberGymState(task="old task", agent_id="old-agent", checksum="old-sum")
    agent = _Agent()

    refreshed = agent.refresh_state(
        state,
        "current task",
        task_id="arvo:1065",
        agent_id="new-agent",
        checksum="new-sum",
        server_url="http://server:8667",
        difficulty="level1",
        error_txt="crash log",
        description="the vulnerability",
        trace_run_dir="/home/loong/runs/traces/one",
    )

    assert refreshed.task == "current task"
    assert refreshed.task_id == "arvo:1065"
    assert refreshed.agent_id == "new-agent"
    assert refreshed.checksum == "new-sum"
    assert refreshed.server_url == "http://server:8667"
    assert refreshed.error_txt == "crash log"
    assert refreshed.vulnerability_description == "the vulnerability"
    assert refreshed.metadata["trace_run_dir"] == "/home/loong/runs/traces/one"


def test_refresh_state_keeps_previous_values_without_kwargs():
    state = CyberGymState(task="old task", agent_id="old-agent", checksum="old-sum")
    agent = _Agent()

    refreshed = agent.refresh_state(state, "current task", **{})

    assert refreshed.agent_id == "old-agent"
    assert refreshed.checksum == "old-sum"


def test_refresh_state_keeps_patch_diff_outside_level1():
    agent = _Agent()
    kwargs: dict[str, Any] = {"difficulty": "level1", "patch_diff": "diff"}
    state = CyberGymState(task="t", patch_diff="old")
    refreshed = agent.refresh_state(state, "t", **kwargs)
    assert refreshed.patch_diff == "old"

    state2 = CyberGymState(task="t", patch_diff="old")
    refreshed2 = agent.refresh_state(state2, "t", **{"difficulty": "level2", "patch_diff": "diff"})
    assert refreshed2.patch_diff == "diff"
