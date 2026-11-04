"""Conservative context-window inference for common model identifiers.

Resolution order:
1. Explicit prefix table (most specific matches, e.g. gpt-4o vs "openai" preset)
2. Family preset (broader model-family defaults)
3. Caller-supplied fallback
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class _ContextPattern:
    prefixes: tuple[str, ...]
    window: int


# Explicit patterns for context window inference. Order matters:
# more specific prefixes MUST come before broader ones.
# These patterns take priority over family presets.
_PATTERNS: tuple[_ContextPattern, ...] = (
    # gpt-4.1 family — 1M context
    _ContextPattern(
        prefixes=(
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
        ),
        window=1_047_576,
    ),
    # DeepSeek V4 family — 1M context on the wire, but 256k here: the engine
    # keeps the whole history in memory, and a 1M window with several runs in
    # parallel exhausts the WSL memory limit (OOM-killed agents).
    _ContextPattern(
        prefixes=(
            "deepseek-v4",
            "deepseek-v4.1",
            "deepseek-v4-flash",
        ),
        window=262_144,
    ),
    # gpt-4o family — 128k context
    _ContextPattern(
        prefixes=(
            "gpt-4o",
            "gpt-4o-mini",
            "chatgpt-4o-latest",
        ),
        window=128_000,
    ),
    # o-series — 200k context
    _ContextPattern(
        prefixes=(
            "o3",
            "o3-mini",
            "o4-mini",
            "codex-mini-latest",
        ),
        window=200_000,
    ),
    # gpt-4-turbo — 128k
    _ContextPattern(
        prefixes=(
            "gpt-4-turbo",
            "gpt-4-0125-preview",
            "gpt-4-1106-preview",
        ),
        window=128_000,
    ),
    # gpt-4-32k
    _ContextPattern(
        prefixes=(
            "gpt-4-32k",
            "gpt-4-32k-0314",
            "gpt-4-32k-0613",
        ),
        window=32_768,
    ),
    # original gpt-4 — 8k (must come after all more specific gpt-4 variants)
    _ContextPattern(
        prefixes=(
            "gpt-4",
            "gpt-4-0613",
            "gpt-4-0314",
        ),
        window=8_192,
    ),
    # gpt-3.5-turbo
    _ContextPattern(
        prefixes=(
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-0125",
            "gpt-3.5-turbo-1106",
            "gpt-3.5-turbo-16k",
        ),
        window=16_385,
    ),
)


def _normalize(model_name: Optional[str]) -> str:
    return str(model_name or "").strip().lower()


def infer_context_window(
    model_name: Optional[str], *, fallback: Optional[int] = None
) -> Optional[int]:
    """Infer a conservative context window from a model identifier.

    Resolution order:
    1. Explicit prefix table (most specific matches)
    2. Family preset (broader model-family defaults)
    3. Caller-supplied fallback
    """
    normalized = _normalize(model_name)
    if not normalized:
        return fallback

    # 1. Check explicit prefix table first (more specific than presets)
    for pattern in _PATTERNS:
        if any(normalized.startswith(prefix) for prefix in pattern.prefixes):
            return int(pattern.window)

    # 2. Try family preset resolution (lazy import to avoid circular dependency)
    try:
        from ..harness._presets import resolve_builtin_preset

        preset = resolve_builtin_preset(model_name)
        if preset.context_policy.context_window_hint is not None:
            return int(preset.context_policy.context_window_hint)
    except ValueError:
        pass

    return fallback


def known_context_patterns() -> Iterable[tuple[tuple[str, ...], int]]:
    """Return the combined preset-derived and legacy prefix table for debugging and tests."""

    from ..harness._presets import known_family_presets

    for preset in known_family_presets():
        if preset.context_policy.context_window_hint is not None:
            yield preset.model_matchers, preset.context_policy.context_window_hint

    # Yield legacy patterns
    for pattern in _PATTERNS:
        yield pattern.prefixes, pattern.window
