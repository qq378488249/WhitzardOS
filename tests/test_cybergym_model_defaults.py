from __future__ import annotations

import pytest

from qitos.benchmark.cybergym.agent.cli import (
    DEFAULT_API_TIMEOUT,
    DEFAULT_MAX_TOKENS,
    GLM_DEFAULT_MAX_TOKENS,
    REASONING_API_TIMEOUT,
    REASONING_DEFAULT_MAX_TOKENS,
    _create_llm,
    default_max_tokens_for_model,
    default_timeout_for_model,
    infer_family_id,
)


def test_infer_family_id_covers_reasoning_families() -> None:
    assert infer_family_id("glm-4.6") == "glm"
    assert infer_family_id("zai-org/GLM-5.1-FP8") == "glm"
    assert infer_family_id("mimo-v2.6-flash") == "mimo"
    assert infer_family_id("xiaomi/mimo-v2.6-pro") == "mimo"
    assert infer_family_id("deepseek-v4.1-flash") == "deepseek"
    assert infer_family_id("claude-sonnet-4-6") is None


def test_infer_family_id_matches_gateway_model_aliases() -> None:
    # The served name can carry a gateway prefix; the harness preset matcher
    # resolves it by family instead of requiring a known prefix.
    assert infer_family_id("ap-ds-v4-flash") == "deepseek"
    assert infer_family_id("glm5") == "glm"
    assert infer_family_id("minimax") == "minimax"
    assert infer_family_id("mimo-v2.6-pro") == "mimo"


def test_gateway_alias_gets_the_reasoning_budget_and_timeout() -> None:
    assert (
        default_max_tokens_for_model("ap-ds-v4-flash")
        == REASONING_DEFAULT_MAX_TOKENS
    )
    assert default_timeout_for_model("ap-ds-v4-flash") == REASONING_API_TIMEOUT


def test_reasoning_models_get_a_large_output_budget() -> None:
    # Reasoning models spend output budget before emitting the tool call; the
    # 8k default truncates them mid-thought.
    assert default_max_tokens_for_model("mimo-v2.6-flash") == REASONING_DEFAULT_MAX_TOKENS
    assert default_max_tokens_for_model("deepseek-v4.1-flash") == REASONING_DEFAULT_MAX_TOKENS
    assert default_max_tokens_for_model("glm-4.6") == GLM_DEFAULT_MAX_TOKENS
    assert default_max_tokens_for_model("claude-sonnet-4-6") == DEFAULT_MAX_TOKENS


def test_reasoning_models_get_a_longer_request_timeout() -> None:
    assert default_timeout_for_model("mimo-v2.6-flash") == REASONING_API_TIMEOUT
    assert default_timeout_for_model("deepseek-v4.1-flash") == REASONING_API_TIMEOUT
    assert default_timeout_for_model("glm-5.3-flash") == REASONING_API_TIMEOUT
    assert default_timeout_for_model("claude-sonnet-4-6") == DEFAULT_API_TIMEOUT


def test_glm_uses_the_reasoning_output_budget() -> None:
    # GLM spends output tokens on reasoning before emitting a tool call; the
    # old 20k budget cut long generations at exactly 20 000 tokens
    # (finish_reason='length'), so it now shares the reasoning budget.
    assert GLM_DEFAULT_MAX_TOKENS == REASONING_DEFAULT_MAX_TOKENS == 65536
    assert default_max_tokens_for_model("glm-5.3-flash") == 65536
    assert default_max_tokens_for_model("glm-4.6") == 65536


def _build_llm(model: str = "ap-ds-v4-flash", **overrides):
    config = {
        "api_key": "dummy",
        "base_url": "http://127.0.0.1:1/v1",
    }
    config.update(overrides)
    return _create_llm(model, config)


def test_environment_overrides_model_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYBERGYM_MAX_OUTPUT_TOKENS", "12345")
    monkeypatch.setenv("CYBERGYM_LLM_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("CYBERGYM_CONTEXT_WINDOW", "1000000")

    llm = _build_llm()

    assert llm.max_tokens == 12345
    assert llm.timeout == 99
    assert llm.context_window == 1000000


def test_explicit_llm_config_wins_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYBERGYM_MAX_OUTPUT_TOKENS", "12345")
    monkeypatch.setenv("CYBERGYM_LLM_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("CYBERGYM_CONTEXT_WINDOW", "1000000")

    llm = _build_llm(max_tokens=777, timeout=8, context_window=4096)

    assert llm.max_tokens == 777
    assert llm.timeout == 8
    assert llm.context_window == 4096


def test_invalid_environment_override_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYBERGYM_MAX_OUTPUT_TOKENS", "lots")

    with pytest.raises(ValueError, match="CYBERGYM_MAX_OUTPUT_TOKENS"):
        _build_llm()
