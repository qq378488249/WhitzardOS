"""Parser protocol definitions for Engine integration."""

from __future__ import annotations

import html
import json
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, Generic, List, Optional, Protocol, TypeVar

from ..core.decision import Decision


ActionT = TypeVar("ActionT")


class Parser(Protocol, Generic[ActionT]):
    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[ActionT]:
        """Parse raw output into a validated Decision."""


class BaseParser(ABC, Generic[ActionT]):
    contract_id = "base_parser_v1"

    @abstractmethod
    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[ActionT]:
        """Parse raw output into Decision."""


@dataclass
class ParserDiagnostic:
    parser: str
    contract: str
    severity: str
    code: str
    summary: str
    details: str = ""
    repair_instruction: str = ""
    expected_shape: str = ""
    issue_path: Optional[str] = None
    extraction_mode: str = ""
    protocol: str = ""
    selected_parser: str = ""
    fallback_used: bool = False
    parser_attempts: Optional[List[Dict[str, Any]]] = None
    raw_output_preview: str = ""
    raw_output_chars: int = 0
    salvage_applied: bool = False
    salvage_summary: str = ""
    step_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["parser_attempts"] = list(self.parser_attempts or [])
        return payload


def parser_name(parser: Any) -> str:
    if isinstance(parser, str):
        return parser
    if parser is None:
        return "unknown_parser"
    return parser.__class__.__name__


def parser_contract(parser: Any) -> str:
    if isinstance(parser, str):
        return parser
    if parser is None:
        return "unknown_parser_v1"
    return str(getattr(parser, "contract_id", parser.__class__.__name__.lower()))


def parser_raw_preview(raw_output: Any, limit: int = 50000) -> str:
    text = str(raw_output or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "... (truncated)"


# Provider-private tool-call markup that leaks into `content` when an
# OpenAI-compatible gateway does not map it to native `tool_calls`. DeepSeek
# models emit `<｜DSML｜tool_calls>` (with U+FF5C fullwidth bars), for example;
# other gateways forward their own protocol tags verbatim (`<tool_result>`,
# `<tool_info>`, often as unterminated nested runs such as
# `</tool_format</tool_size_t</tool_result>`).
_LEAKED_TOOL_MARKUP_RE = re.compile(
    r"</?[｜|]\s*DSML\s*[｜|][^>]*>"
    r"|</?\s*tool_[a-z0-9_]+(?:\s[^>]{0,120})?>?",
    re.IGNORECASE,
)


def strip_leaked_tool_markup(text: str) -> tuple[str, bool]:
    """Strip provider-private tool-call markup from model text.

    Returns ``(cleaned_text, had_markup)``. Text carrying this markup is a
    failed tool call, never a final answer; callers use the flag to keep it
    out of the final-answer path and to route it through parser repair.
    """
    if not text:
        return text, False
    cleaned = _LEAKED_TOOL_MARKUP_RE.sub("", text)
    if cleaned == text:
        return text, False
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, True


# DeepSeek V4 serialises tool calls with private ``<｜DSML｜...>`` tokens when
# an OpenAI-compatible gateway forwards them as plain content instead of
# mapping them to ``tool_calls``. A *complete* block is safe to restore: the
# name and every parameter are explicitly delimited. A truncated block is not,
# so salvage only accepts fully closed outer blocks, invokes, and parameters.
_DSML_TOKEN = r"[｜|]\s*DSML\s*[｜|]"
_DSML_TOOL_CALLS_OPEN_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*tool_calls\s*>", re.IGNORECASE
)
_DSML_TOOL_CALLS_BLOCK_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*tool_calls\s*>(.*?)</{_DSML_TOKEN}\s*tool_calls\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_INVOKE_OPEN_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*invoke\b[^>]*>", re.IGNORECASE
)
_DSML_INVOKE_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*invoke\b([^>]*)>(.*?)</{_DSML_TOKEN}\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_PARAMETER_OPEN_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*parameter\b[^>]*>", re.IGNORECASE
)
_DSML_PARAMETER_RE = re.compile(
    rf"<{_DSML_TOKEN}\s*parameter\b([^>]*)>"
    rf"(.*?)</{_DSML_TOKEN}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_ATTR_NAME_RE = re.compile(r"""\bname\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_DSML_ATTR_STRING_RE = re.compile(
    r"""\bstring\s*=\s*["']?(?:true|1)["']?""", re.IGNORECASE
)


def has_dsml_tool_call_markup(text: str) -> bool:
    """True when the text carries DeepSeek DSML tool-call markup."""
    source = str(text or "")
    return bool(
        _DSML_TOOL_CALLS_OPEN_RE.search(source)
        or _DSML_INVOKE_OPEN_RE.search(source)
    )


def salvage_dsml_tool_calls(text: str) -> Optional[List[Dict[str, Any]]]:
    """Restore complete DeepSeek DSML tool calls into OpenAI-style tool calls.

    Returns ``None`` (never a partial result) when the payload is absent,
    truncated, or malformed. Incomplete markup stays in the response text so
    the leaked-markup path treats it as a failed generation.
    """
    source = str(text or "")
    if not _DSML_TOOL_CALLS_OPEN_RE.search(source):
        return None
    blocks = _DSML_TOOL_CALLS_BLOCK_RE.findall(source)
    if not blocks or len(blocks) != len(_DSML_TOOL_CALLS_OPEN_RE.findall(source)):
        return None

    calls: List[Dict[str, Any]] = []
    for block in blocks:
        invoke_matches = _DSML_INVOKE_RE.findall(block)
        if not invoke_matches:
            return None
        if len(invoke_matches) != len(_DSML_INVOKE_OPEN_RE.findall(block)):
            return None
        for invoke_attrs, invoke_body in invoke_matches:
            name = _dsml_attr(invoke_attrs, _DSML_ATTR_NAME_RE)
            if not name:
                return None
            parameter_matches = _DSML_PARAMETER_RE.findall(invoke_body)
            if len(parameter_matches) != len(
                _DSML_PARAMETER_OPEN_RE.findall(invoke_body)
            ):
                return None
            args: Dict[str, Any] = {}
            for parameter_attrs, parameter_value in parameter_matches:
                key = _dsml_attr(parameter_attrs, _DSML_ATTR_NAME_RE)
                if not key:
                    return None
                args[key] = _coerce_dsml_value(
                    parameter_value,
                    string_typed=bool(_DSML_ATTR_STRING_RE.search(parameter_attrs)),
                )
            calls.append(
                {
                    "id": f"call_dsml_{len(calls) + 1}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            )
    return calls or None


def remove_dsml_tool_call_blocks(text: str) -> str:
    """Remove complete DSML tool-call blocks from text, keeping the prose."""
    return _DSML_TOOL_CALLS_BLOCK_RE.sub("", str(text or "")).strip()


def _dsml_attr(attrs: str, pattern: "re.Pattern[str]") -> str:
    match = pattern.search(str(attrs or ""))
    return match.group(1).strip() if match else ""


def _coerce_dsml_value(value: str, *, string_typed: bool) -> Any:
    text = html.unescape(str(value or "")).strip()
    if string_typed:
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def build_parser_diagnostics(
    *,
    parser: Any,
    severity: str,
    code: str,
    summary: str,
    raw_output: Any = None,
    details: str = "",
    repair_instruction: str = "",
    expected_shape: str = "",
    issue_path: Optional[str] = None,
    extraction_mode: str = "",
    protocol: str = "",
    selected_parser: str = "",
    fallback_used: bool = False,
    parser_attempts: Optional[List[Dict[str, Any]]] = None,
    salvage_applied: bool = False,
    salvage_summary: str = "",
    step_id: Optional[int] = None,
) -> Dict[str, Any]:
    diag = ParserDiagnostic(
        parser=parser_name(parser),
        contract=parser_contract(parser),
        severity=str(severity),
        code=str(code),
        summary=str(summary).strip(),
        details=str(details).strip(),
        repair_instruction=str(repair_instruction).strip(),
        expected_shape=str(expected_shape).strip(),
        issue_path=(
            str(issue_path).strip()
            if isinstance(issue_path, str) and issue_path.strip()
            else None
        ),
        extraction_mode=str(extraction_mode).strip(),
        protocol=str(protocol).strip(),
        selected_parser=str(selected_parser).strip(),
        fallback_used=bool(fallback_used),
        parser_attempts=list(parser_attempts or []),
        raw_output_preview=parser_raw_preview(raw_output),
        raw_output_chars=len(str(raw_output or "")),
        salvage_applied=bool(salvage_applied),
        salvage_summary=str(salvage_summary).strip(),
        step_id=step_id,
    )
    return diag.to_dict()


def parser_wait_decision(
    *,
    parser: Any,
    code: str,
    summary: str,
    raw_output: Any = None,
    details: str = "",
    repair_instruction: str = "",
    expected_shape: str = "",
    issue_path: Optional[str] = None,
    extraction_mode: str = "",
    protocol: str = "",
    selected_parser: str = "",
    fallback_used: bool = False,
    parser_attempts: Optional[List[Dict[str, Any]]] = None,
    salvage_applied: bool = False,
    salvage_summary: str = "",
    rationale: Optional[str] = None,
    step_id: Optional[int] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> Decision[Any]:
    diagnostics = build_parser_diagnostics(
        parser=parser,
        severity="error",
        code=code,
        summary=summary,
        raw_output=raw_output,
        details=details,
        repair_instruction=repair_instruction,
        expected_shape=expected_shape,
        issue_path=issue_path,
        extraction_mode=extraction_mode,
        protocol=protocol,
        selected_parser=selected_parser,
        fallback_used=fallback_used,
        parser_attempts=parser_attempts,
        salvage_applied=salvage_applied,
        salvage_summary=salvage_summary,
        step_id=step_id,
    )
    meta = dict(extra_meta or {})
    meta["parser_error"] = True
    meta["parser_feedback"] = (
        diagnostics["repair_instruction"] or diagnostics["summary"]
    )
    meta["parser_diagnostics"] = diagnostics
    return Decision.wait(rationale=rationale or diagnostics["summary"], meta=meta)


def attach_parser_warning(
    meta: Optional[Dict[str, Any]],
    *,
    parser: Any,
    code: str,
    summary: str,
    raw_output: Any = None,
    details: str = "",
    expected_shape: str = "",
    extraction_mode: str = "",
    protocol: str = "",
    selected_parser: str = "",
    fallback_used: bool = False,
    parser_attempts: Optional[List[Dict[str, Any]]] = None,
    salvage_applied: bool = False,
    salvage_summary: str = "",
    step_id: Optional[int] = None,
) -> Dict[str, Any]:
    merged = dict(meta or {})
    diagnostics = build_parser_diagnostics(
        parser=parser,
        severity="warning",
        code=code,
        summary=summary,
        raw_output=raw_output,
        details=details,
        repair_instruction="",
        expected_shape=expected_shape,
        extraction_mode=extraction_mode,
        protocol=protocol,
        selected_parser=selected_parser,
        fallback_used=fallback_used,
        parser_attempts=parser_attempts,
        salvage_applied=salvage_applied,
        salvage_summary=salvage_summary,
        step_id=step_id,
    )
    merged["parser_warning"] = diagnostics["salvage_summary"] or diagnostics["summary"]
    merged["parser_diagnostics"] = diagnostics
    return merged


def normalize_parser_diagnostics(
    meta: Optional[Dict[str, Any]],
    *,
    parser: Any,
    raw_output: Any = None,
    step_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(meta, dict):
        return None
    diagnostics = meta.get("parser_diagnostics")
    if isinstance(diagnostics, dict):
        normalized = dict(diagnostics)
        normalized.setdefault("parser", parser_name(parser))
        normalized.setdefault("contract", parser_contract(parser))
        normalized.setdefault("raw_output_preview", parser_raw_preview(raw_output))
        normalized.setdefault("raw_output_chars", len(str(raw_output or "")))
        normalized.setdefault("step_id", step_id)
        normalized.setdefault(
            "severity", "error" if meta.get("parser_error") else "warning"
        )
        normalized.setdefault("extraction_mode", "")
        normalized.setdefault("protocol", "")
        normalized.setdefault("selected_parser", parser_name(parser))
        normalized.setdefault("fallback_used", False)
        normalized.setdefault("parser_attempts", [])
        return normalized
    if not meta.get("parser_error") and not meta.get("parser_warning"):
        return None
    severity = "error" if meta.get("parser_error") else "warning"
    feedback = str(
        meta.get("parser_feedback") or meta.get("parser_warning") or ""
    ).strip()
    return build_parser_diagnostics(
        parser=parser,
        severity=severity,
        code="legacy_parser_feedback",
        summary=feedback
        or ("Parser error." if severity == "error" else "Parser warning."),
        raw_output=raw_output,
        details="Parser returned legacy parser_error/parser_warning fields without structured diagnostics.",
        repair_instruction=feedback if severity == "error" else "",
        expected_shape="See the configured parser contract for required output fields.",
        step_id=step_id,
    )


__all__ = [
    "Parser",
    "BaseParser",
    "ParserDiagnostic",
    "parser_name",
    "parser_contract",
    "parser_raw_preview",
    "build_parser_diagnostics",
    "parser_wait_decision",
    "attach_parser_warning",
    "normalize_parser_diagnostics",
]
