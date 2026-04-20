"""Tests for GPT-5 pricing entries, cached-input billing, and the
CalculateCost effect / default-handler contract.

These tests do not depend on the legacy ``default_handlers`` helper
(removed in an earlier refactor) — they compose handlers explicitly
with :class:`doeff.WithHandler`, mirroring the supported usage pattern.
"""

import pytest

from doeff import Pass, Resume, WithHandler, do, run
from doeff_openai import (
    CalculateCost,
    CostInfo,
    MODEL_PRICING,
    MissingCachedPricingError,
    TokenUsage,
    UnknownModelPricingError,
    calculate_cost,
)
from doeff_openai.handlers.production import openai_production_handler


# ---------------------------------------------------------------------------
# Pricing table — GPT-5 family exists and matches published rates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "input_rate", "output_rate", "cached_rate"),
    [
        ("gpt-5",        0.00125, 0.010,   0.000125),
        ("gpt-5-mini",   0.00025, 0.002,   0.000025),
        ("gpt-5-nano",   0.00005, 0.0004,  0.000005),
        ("gpt-5.4",      0.00250, 0.015,   0.000250),
        ("gpt-5.4-mini", 0.00075, 0.0045,  0.0000750),
        ("gpt-5.4-nano", 0.00020, 0.00125, 0.0000200),
    ],
)
def test_gpt5_family_rates(model, input_rate, output_rate, cached_rate):
    pricing = MODEL_PRICING[model]
    assert pricing.input_price_per_1k == input_rate
    assert pricing.output_price_per_1k == output_rate
    assert pricing.cached_input_price_per_1k == cached_rate


# ---------------------------------------------------------------------------
# calculate_cost — sync path
# ---------------------------------------------------------------------------


def test_calculate_cost_gpt5_mini_no_cache():
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    cost = calculate_cost("gpt-5-mini", usage)
    expected = (1000 / 1000) * 0.00025 + (500 / 1000) * 0.002
    assert cost.total_cost == pytest.approx(expected)
    assert cost.model == "gpt-5-mini"


def test_calculate_cost_gpt5_with_cached_tokens():
    usage = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cached_prompt_tokens=500,
    )
    cost = calculate_cost("gpt-5", usage)
    expected = (
        (500 / 1000) * 0.00125      # fresh input
        + (500 / 1000) * 0.000125   # cached input
        + (500 / 1000) * 0.010      # output
    )
    assert cost.total_cost == pytest.approx(expected)


def test_calculate_cost_unknown_model_raises():
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    with pytest.raises(UnknownModelPricingError):
        calculate_cost("gpt-99-ultra", usage)


def test_calculate_cost_cached_on_legacy_model_raises():
    # gpt-3.5-turbo has no cached_input_price_per_1k — reporting
    # cached tokens for it must fail loudly, never silently overbill.
    usage = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cached_prompt_tokens=200,
    )
    with pytest.raises(MissingCachedPricingError) as exc:
        calculate_cost("gpt-3.5-turbo", usage)
    assert exc.value.cached_tokens == 200


def test_calculate_cost_legacy_model_without_cached_tokens_still_works():
    # Backward compat: legacy models without cached pricing still bill
    # correctly when no cached tokens are reported.
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    cost = calculate_cost("gpt-3.5-turbo", usage)
    expected = (1000 / 1000) * 0.0005 + (500 / 1000) * 0.0015
    assert cost.total_cost == pytest.approx(expected)


# ---------------------------------------------------------------------------
# CalculateCost effect — default handler contract
# ---------------------------------------------------------------------------


@do
def _ask_cost(model, usage):
    cost = yield CalculateCost(model=model, token_usage=usage)
    return cost


def test_calculate_cost_effect_known_model_resumes_with_cost_info():
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    result = run(WithHandler(openai_production_handler, _ask_cost("gpt-5-mini", usage)))
    assert isinstance(result, CostInfo)
    assert result.model == "gpt-5-mini"
    assert result.total_cost > 0


def test_calculate_cost_effect_unknown_model_passes_to_outer_handler():
    # User installs an outer handler that substitutes a zero-cost for
    # any unknown model. The default handler must Pass, letting the
    # outer override take effect.
    @do
    def zero_cost_override(effect, k):
        if isinstance(effect, CalculateCost):
            return (yield Resume(k, CostInfo(
                input_cost=0.0, output_cost=0.0, total_cost=0.0,
                model=effect.model, token_usage=effect.token_usage,
            )))
        yield Pass(effect, k)

    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    result = run(
        WithHandler(
            zero_cost_override,
            WithHandler(openai_production_handler, _ask_cost("future-model-v9", usage)),
        )
    )
    assert result.total_cost == 0.0
    assert result.model == "future-model-v9"


def test_calculate_cost_effect_unknown_model_no_outer_handler_raises():
    # With only the default handler installed, an unknown model Pass-es
    # out of every handler and becomes an unhandled-effect error. This
    # is the loud-fail property: silent fall-back is impossible.
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    with pytest.raises(RuntimeError, match="no handler found for effect"):
        run(WithHandler(openai_production_handler, _ask_cost("future-model-v9", usage)))


def test_calculate_cost_effect_cached_on_legacy_model_passes():
    # Cached tokens on a model without cached pricing also Pass-es so
    # the user can decide (override with a substitute, or let it fail).
    usage = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cached_prompt_tokens=100,
    )
    with pytest.raises(RuntimeError, match="no handler found for effect"):
        run(WithHandler(openai_production_handler, _ask_cost("gpt-3.5-turbo", usage)))


# ---------------------------------------------------------------------------
# TokenUsage cached-token fields
# ---------------------------------------------------------------------------


def test_token_usage_fresh_input_tokens_subtracts_cached():
    usage = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cached_prompt_tokens=300,
    )
    assert usage.fresh_input_tokens == 700


def test_token_usage_default_cached_zero():
    usage = TokenUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)
    assert usage.cached_prompt_tokens == 0
    assert usage.fresh_input_tokens == 1000
