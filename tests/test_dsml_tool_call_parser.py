from __future__ import annotations

from qitos.engine.parser import (
    remove_dsml_tool_call_blocks,
    salvage_dsml_tool_calls,
    strip_leaked_tool_markup,
)
from qitos.kit.parser.dsml_tool_call_parser import DsmlToolCallParser

_COMPLETE = (
    "Let me add these.\n\n"
    "<｜DSML｜tool_calls>\n"
    '<｜DSML｜invoke name="add">\n'
    '<｜DSML｜parameter name="a">20</｜DSML｜parameter>\n'
    '<｜DSML｜parameter name="b">22</｜DSML｜parameter>\n'
    "</｜DSML｜invoke>\n"
    '<｜DSML｜invoke name="add">\n'
    '<｜DSML｜parameter name="a" string="true">1</｜DSML｜parameter>\n'
    '<｜DSML｜parameter name="b" string="true">2</｜DSML｜parameter>\n'
    "</｜DSML｜invoke>\n"
    "</｜DSML｜tool_calls>\n"
    "Done."
)


def test_salvage_restores_complete_dsml_tool_calls() -> None:
    calls = salvage_dsml_tool_calls(_COMPLETE)

    assert calls is not None
    assert [call["function"]["name"] for call in calls] == ["add", "add"]
    assert calls[0]["function"]["arguments"] == '{"a": 20, "b": 22}'
    # string="true" keeps the parameter as text.
    assert calls[1]["function"]["arguments"] == '{"a": "1", "b": "2"}'


def test_remove_blocks_keeps_surrounding_prose() -> None:
    cleaned = remove_dsml_tool_call_blocks(_COMPLETE)

    assert "DSML" not in cleaned
    assert cleaned.startswith("Let me add these.")
    assert cleaned.endswith("Done.")


def test_salvage_rejects_truncated_payloads() -> None:
    no_outer_close = (
        '<｜DSML｜tool_calls>\n<｜DSML｜invoke name="add">\n'
        '<｜DSML｜parameter name="a">20</｜DSML｜parameter>\n'
        "</｜DSML｜invoke>"
    )
    no_invoke_close = (
        '<｜DSML｜tool_calls>\n<｜DSML｜invoke name="add">\n'
        '<｜DSML｜parameter name="a">20</｜DSML｜parameter>\n'
        "</｜DSML｜tool_calls>"
    )
    no_parameter_close = (
        '<｜DSML｜tool_calls>\n<｜DSML｜invoke name="add">\n'
        '<｜DSML｜parameter name="a">20\n'
        "</｜DSML｜invoke>\n</｜DSML｜tool_calls>"
    )

    assert salvage_dsml_tool_calls(no_outer_close) is None
    assert salvage_dsml_tool_calls(no_invoke_close) is None
    assert salvage_dsml_tool_calls(no_parameter_close) is None
    assert salvage_dsml_tool_calls("plain text") is None


def test_parser_returns_an_action_for_a_complete_block() -> None:
    decision = DsmlToolCallParser().parse(_COMPLETE)

    assert decision.mode == "act"
    assert decision.actions == [
        {"name": "add", "args": {"a": 20, "b": 22}},
        {"name": "add", "args": {"a": "1", "b": "2"}},
    ]
    assert decision.meta["output_format"] == "deepseek_dsml"


def test_parser_returns_repair_feedback_for_an_incomplete_block() -> None:
    decision = DsmlToolCallParser().parse(
        '<｜DSML｜tool_calls>\n<｜DSML｜invoke name="add">'
    )

    assert decision.mode == "wait"
    assert decision.meta["parser_error"] is True
    assert (
        decision.meta["parser_diagnostics"]["code"]
        == "incomplete_dsml_tool_call"
    )


def test_parser_reports_absent_dsml_payload() -> None:
    decision = DsmlToolCallParser().parse("plain natural-language answer")

    assert decision.mode == "wait"
    assert decision.meta["parser_error"] is True
    assert decision.meta["parser_diagnostics"]["code"] == "no_dsml_payload"


def test_orphan_dsml_closing_tag_is_leaked_markup() -> None:
    cleaned, had_markup = strip_leaked_tool_markup(
        "analysis</｜DSML｜tool_calls></｜DSML｜invoke>"
    )

    assert had_markup is True
    assert "DSML" not in cleaned
    assert cleaned == "analysis"
