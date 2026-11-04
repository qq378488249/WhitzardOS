from __future__ import annotations

from qitos.benchmark.cybergym.agent.cli import (
    DEFAULT_API_TIMEOUT,
    DEFAULT_MAX_TOKENS,
    GLM_DEFAULT_MAX_TOKENS,
    REASONING_API_TIMEOUT,
    REASONING_DEFAULT_MAX_TOKENS,
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
    assert default_timeout_for_model("claude-sonnet-4-6") == DEFAULT_API_TIMEOUT
