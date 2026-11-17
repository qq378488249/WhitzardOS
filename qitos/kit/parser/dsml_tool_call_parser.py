"""Parser for DeepSeek V4 DSML tool-call markup."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from qitos.core.decision import Decision
from qitos.engine.parser import (
    BaseParser,
    has_dsml_tool_call_markup,
    parser_wait_decision,
    salvage_dsml_tool_calls,
)

_EXPECTED_SHAPE = (
    '<｜DSML｜tool_calls><｜DSML｜invoke name="tool_name">'
    '<｜DSML｜parameter name="key">value</｜DSML｜parameter>'
    "</｜DSML｜invoke></｜DSML｜tool_calls>"
)


class DsmlToolCallParser(BaseParser[Dict[str, Any]]):
    """Parse a complete DeepSeek DSML payload into canonical actions.

    Some OpenAI-compatible gateways forward DeepSeek V4's private tool-call
    tokens as plain content instead of mapping them to ``tool_calls``. Only a
    fully closed block is accepted; a truncated payload stays unparseable so
    the engine routes it through parser repair instead of acting on a
    half-written call.
    """

    contract_id = "dsml_tool_call_v1"

    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[Dict[str, Any]]:
        _ = context
        text = str(raw_output or "")
        tool_calls = salvage_dsml_tool_calls(text)
        if tool_calls:
            actions: List[Dict[str, Any]] = []
            for call in tool_calls:
                function = call.get("function") or {}
                actions.append(
                    {
                        "name": str(function.get("name") or "").strip(),
                        "args": json.loads(str(function.get("arguments") or "{}")),
                    }
                )
            return Decision.act(
                actions=actions,
                meta={"output_format": "deepseek_dsml"},
            )

        if has_dsml_tool_call_markup(text):
            return parser_wait_decision(
                parser=self,
                code="incomplete_dsml_tool_call",
                summary="DeepSeek DSML tool-call markup was incomplete.",
                raw_output=text,
                details=(
                    "The response contained DSML tool-call markup but not a "
                    "fully closed tool_calls/invoke/parameter block."
                ),
                repair_instruction=(
                    "Emit a native tool call, or complete every DSML invoke and "
                    "parameter block before closing </｜DSML｜tool_calls>."
                ),
                expected_shape=_EXPECTED_SHAPE,
                extraction_mode="regex",
            )

        return parser_wait_decision(
            parser=self,
            code="no_dsml_payload",
            summary="No DeepSeek DSML tool-call payload was found.",
            raw_output=text,
            details="The response did not contain DSML tool-call markup.",
            repair_instruction="Return a native tool call or the expected decision format.",
            expected_shape=_EXPECTED_SHAPE,
            extraction_mode="regex",
        )
