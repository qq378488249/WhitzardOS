from types import SimpleNamespace

from qitos import Action, AgentModule, Decision, Engine, ToolRegistry, tool
from qitos.core.history import History, HistoryMessage
from qitos.core.model_response import ModelResponse
from qitos.core.state import StateSchema
from qitos.engine import RuntimeBudget
from qitos.engine.states import StepRecord
from qitos.kit.parser import ReActTextParser


class _HistoryCapture(History):
    def __init__(self):
        self.messages: list[HistoryMessage] = []

    def append(self, message: HistoryMessage) -> None:
        self.messages.append(message)

    def retrieve(self, query=None, state=None, observation=None):
        _ = query, state, observation
        return list(self.messages)

    def summarize(self, max_items: int = 5) -> str:
        _ = max_items
        return ""

    def evict(self) -> int:
        return 0

    def reset(self, run_id=None) -> None:
        _ = run_id
        self.messages = []


class _State(StateSchema):
    pass


class _ToolCallAgent(AgentModule[_State, dict, Action]):
    def __init__(self, llm):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        super().__init__(tool_registry=registry, llm=llm)
        self.model_parser = ReActTextParser()
        self.history = _HistoryCapture()

    def init_state(self, task: str, **kwargs):
        _ = kwargs
        return _State(task=task, max_steps=2)

    def build_system_prompt(self, state: _State):
        _ = state
        return "System prompt"

    def prepare(self, state: _State) -> str:
        _ = state
        return "solve"

    def decide(self, state: _State, observation: dict):
        _ = observation
        if state.current_step > 0:
            return Decision.final("done")
        return None

    def reduce(self, state: _State, observation: dict, decision: Decision[Action]):
        _ = observation, decision
        return state


def test_extract_response_text_preserves_object_message_content_when_tool_calls_exist():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime
    raw = SimpleNamespace(
        message=SimpleNamespace(
            content="Conclusion: likely 1-byte trigger. Next: write and submit.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                }
            ],
        )
    )

    text = runtime._extract_response_text(raw)

    assert text == "Conclusion: likely 1-byte trigger. Next: write and submit."


def test_extract_response_text_uses_reasoning_content_when_content_is_empty():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime
    raw = SimpleNamespace(
        message=SimpleNamespace(
            content=None,
            reasoning_content="Conclusion: the checksum logic is the trigger. Next: write a candidate.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                }
            ],
        )
    )

    text = runtime._extract_response_text(raw)

    assert text == "Conclusion: the checksum logic is the trigger. Next: write a candidate."


def test_extract_response_text_does_not_fall_back_to_provider_repr():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime
    raw = SimpleNamespace(
        message=SimpleNamespace(
            content=None,
            reasoning="long chain of thought cut at the output limit",
            tool_calls=None,
        ),
        finish_reason="length",
    )

    assert runtime._extract_response_text(raw) == ""


def test_truncated_generation_is_not_treated_as_final():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime

    # content=None, tool_calls=None, finish_reason='length': the provider ran
    # out of output budget, so this must not end the run as a final answer.
    record = StepRecord(step_id=0)
    decision = runtime.normalize_decision(
        ModelResponse(text="", finish_reason="length", tool_calls=None),
        step=0,
        record=record,
    )

    assert decision.mode == "wait"
    assert decision.meta["truncated"] is True
    assert record.decision_source == "output_truncated"

    # Reasoning may already have leaked into text; the length stop still wins.
    record = StepRecord(step_id=1)
    decision = runtime.normalize_decision(
        ModelResponse(text="still thinking...", finish_reason="length", tool_calls=None),
        step=1,
        record=record,
    )

    assert decision.mode == "wait"
    assert record.decision_source == "output_truncated"


def test_truncated_step_continues_to_the_next_model_call():
    class _ModelOnlyAgent(_ToolCallAgent):
        def decide(self, state: _State, observation: dict):
            _ = state, observation
            return None

    class _TruncateThenActModel:
        model = "demo-model"
        qitos_harness_metadata = {
            "tool_policy": {"native_tool_call_preferred": True}
        }

        def __init__(self):
            self.calls = 0

        def __call__(self, messages):
            _ = messages
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    message=SimpleNamespace(content=None, tool_calls=None),
                    finish_reason="length",
                )
            return SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                        }
                    ],
                ),
                finish_reason="tool_calls",
            )

    llm = _TruncateThenActModel()
    agent = _ModelOnlyAgent(llm=llm)
    Engine(agent=agent, budget=RuntimeBudget(max_steps=2)).run("compute")

    # The truncated first step must not end the run: the loop re-enters
    # DECIDE and the second model call gets to act.
    assert llm.calls == 2


def test_native_tool_call_history_keeps_assistant_text_and_tool_calls():
    class _ObjectResponseModel:
        model = "demo-model"
        qitos_harness_metadata = {
            "tool_policy": {"native_tool_call_preferred": True}
        }

        def __call__(self, messages):
            _ = messages
            return SimpleNamespace(
                message=SimpleNamespace(
                    content="Conclusion: likely 1-byte trigger. Next: use add.",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                        }
                    ],
                ),
                finish_reason="tool_calls",
            )

    agent = _ToolCallAgent(llm=_ObjectResponseModel())
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=2)).run("compute")

    assert result.state.final_result == "done"
    assistant_messages = [m for m in agent.history.messages if m.role == "assistant"]
    assert assistant_messages
    first = assistant_messages[0]
    assert first.content == "Conclusion: likely 1-byte trigger. Next: use add."
    assert first.tool_calls
    assert first.tool_calls[0]["function"]["name"] == "add"


class _NativeTextModel:
    model = "demo-model"
    qitos_harness_metadata = {"tool_policy": {"native_tool_call_preferred": True}}


def _native_text_runtime():
    engine = Engine(
        agent=_ToolCallAgent(llm=_NativeTextModel()),
        budget=RuntimeBudget(max_steps=1),
    )
    return engine._model_runtime


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=None, finish_reason="stop")


def test_native_plain_text_conclusion_is_still_a_final_answer():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response("All checks are done; nothing left to do."),
        step=0,
        record=record,
    )

    assert decision.mode == "final"
    assert decision.final_answer == "All checks are done; nothing left to do."
    assert record.decision_source == "native_text_final"


def test_leaked_dsml_tool_markup_is_not_a_final_answer():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response(
            "Let me write the script to build the file:\n\n"
            "<｜DSML｜tool_calls>\n<｜DSML｜tool_calls>"
        ),
        step=0,
        record=record,
    )

    assert decision.mode == "wait"
    assert decision.meta["parser_error"] is True
    assert record.decision_source == "native_text_tool_markup"


def test_leaked_provider_tool_markup_is_not_a_final_answer():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response(
            "<tool_result>[submit_poc(poc_path=pocs/poc_capture_group.bin]"
            "</tool_result>\n\n<tool_results>{\"status\": \"success\", "
            "\"poc_path\": \"pocs/poc_capture_group.bin\"}</tool_results>"
        ),
        step=0,
        record=record,
    )

    assert decision.mode == "wait"
    assert decision.meta["parser_error"] is True
    assert decision.meta["leaked_tool_markup"] is True
    assert record.decision_source == "native_text_tool_markup"


def test_strip_leaked_tool_markup_handles_tool_protocol_tags():
    from qitos.engine.parser import strip_leaked_tool_markup

    cleaned, had_markup = strip_leaked_tool_markup(
        "offset=200) ---</tool_format</tool_size_t</tool_result>\n"
        "<tool_info>done</tool_info>"
    )

    assert had_markup is True
    assert "tool_" not in cleaned
    assert "offset=200)" in cleaned


def _degenerate_text() -> str:
    return "at opentype-saniser.h:85 " * 60


def test_degenerate_repeated_text_is_not_a_final_answer():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response(_degenerate_text()), step=0, record=record
    )

    assert decision.mode == "wait"
    assert decision.meta["parser_error"] is True
    assert decision.meta["degenerate"] is True
    assert record.decision_source == "native_text_degenerate"


def test_degenerate_text_ends_with_a_marker_not_the_loop():
    runtime = _native_text_runtime()

    decisions = [
        runtime.normalize_decision(
            _text_response(_degenerate_text()),
            step=step,
            record=StepRecord(step_id=step),
        )
        for step in range(3)
    ]

    assert [decision.mode for decision in decisions] == ["wait", "wait", "final"]
    degraded = decisions[-1]
    assert degraded.meta["decision_source"] == "native_text_final_degraded"
    assert degraded.meta["degenerate"] is True
    assert degraded.final_answer.startswith("[degraded]")
    assert "opentype-saniser" not in degraded.final_answer


def test_degenerate_text_with_a_final_label_is_still_retried():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response("Final Answer: " + _degenerate_text()),
        step=0,
        record=record,
    )

    assert decision.mode == "wait"
    assert record.decision_source == "native_text_degenerate"


def test_long_varied_prose_is_still_a_final_answer():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)
    prose = (
        "The trigger is a one-byte overwrite in the parser. I reproduced it "
        "with the staged binary under gdb: the write lands on the chunk header "
        "and the crash is deterministic. The candidate PoC is saved as "
        "pocs/poc_rule.bin, and every check I ran shows the fixed build stays "
        "clean while the vulnerable build aborts. Nothing else is left to try, "
        "so I am reporting this as the final answer for the task at hand."
    )

    decision = runtime.normalize_decision(
        _text_response(prose), step=0, record=record
    )

    assert decision.mode == "final"
    assert decision.final_answer == prose
    assert record.decision_source == "native_text_final"


def test_malformed_structured_output_is_retried_not_finalized():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response('{"action": {"name": "add", "args": {"a": 1'),
        step=0,
        record=record,
    )

    assert decision.mode == "wait"
    assert decision.meta["parser_error"] is True


def test_repeated_unparseable_text_ends_with_degraded_final():
    runtime = _native_text_runtime()
    markup = "Let me write the script:\n\n<｜DSML｜tool_calls>"

    decisions = [
        runtime.normalize_decision(
            _text_response(markup), step=step, record=StepRecord(step_id=step)
        )
        for step in range(3)
    ]

    assert [decision.mode for decision in decisions] == ["wait", "wait", "final"]
    degraded = decisions[-1]
    assert degraded.meta["decision_source"] == "native_text_final_degraded"
    assert degraded.meta["parser_error_streak"] == 3
    assert "<｜DSML｜" not in degraded.final_answer


_DSML_COMPLETE = (
    "Let me add these.\n\n"
    "<｜DSML｜tool_calls>\n"
    '<｜DSML｜invoke name="add">\n'
    '<｜DSML｜parameter name="a">20</｜DSML｜parameter>\n'
    '<｜DSML｜parameter name="b">22</｜DSML｜parameter>\n'
    "</｜DSML｜invoke>\n"
    "</｜DSML｜tool_calls>"
)


def _raw_text_response(text: str):
    return SimpleNamespace(
        message=SimpleNamespace(content=text, tool_calls=None),
        finish_reason="stop",
    )


def test_complete_dsml_tool_call_is_used_after_normal_parsing_fails():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    response = runtime._normalize_model_response(_raw_text_response(_DSML_COMPLETE))
    decision = runtime.normalize_decision(response, step=0, record=record)

    assert decision.mode == "act"
    assert record.decision_source == "deepseek_dsml"
    assert [(action.name, action.args) for action in decision.actions] == [
        ("add", {"a": 20, "b": 22})
    ]
    # The complete block is removed before the text reaches history.
    assert "<｜DSML｜" not in response.text


def test_incomplete_dsml_tool_call_is_retried_not_final():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)
    truncated = (
        '<｜DSML｜tool_calls>\n<｜DSML｜invoke name="add">\n'
        '<｜DSML｜parameter name="a">20</｜DSML｜parameter>'
    )

    response = runtime._normalize_model_response(_raw_text_response(truncated))
    decision = runtime.normalize_decision(response, step=0, record=record)

    assert decision.mode == "wait"
    assert record.decision_source == "native_text_tool_markup"


def test_dsml_tool_call_with_unknown_tool_name_is_not_salvaged():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)
    unknown_tool = _DSML_COMPLETE.replace('name="add"', 'name="xmlAddID"')

    response = runtime._normalize_model_response(
        _raw_text_response(unknown_tool)
    )
    decision = runtime.normalize_decision(response, step=0, record=record)

    assert "dsml_tool_calls" not in response.metadata
    assert decision.mode == "wait"
    assert record.decision_source == "native_text_tool_markup"


def test_orphan_dsml_closing_tag_is_not_a_final_answer():
    runtime = _native_text_runtime()
    record = StepRecord(step_id=0)

    decision = runtime.normalize_decision(
        _text_response("Now I understand.</｜DSML｜tool_calls>"),
        step=0,
        record=record,
    )

    assert decision.mode == "wait"
    assert record.decision_source == "native_text_tool_markup"
