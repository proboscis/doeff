"""Tests for Gemini cost calculation hook and fallback behavior."""

import time
from types import SimpleNamespace
from typing import Any

import pytest

from doeff import EffectGenerator, ExecutionContext, Fail, Local, ProgramInterpreter, do
from doeff_gemini import (
    CostInfo,
    GeminiCallResult,
    GeminiCostEstimate,
)
from doeff_gemini.client import track_api_call


def _fake_response(usage: dict[str, int]) -> Any:
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            text_input_token_count=usage.get("text_input_tokens"),
            text_output_token_count=usage.get("text_output_tokens"),
            image_input_token_count=usage.get("image_input_tokens"),
            image_output_token_count=usage.get("image_output_tokens"),
            total_token_count=usage.get("total_tokens")
            if "total_tokens" in usage
            else sum(v for v in usage.values() if v is not None),
        ),
        response_id="resp-123",
        candidates=[],
    )


@pytest.mark.asyncio
async def test_default_cost_calculator_runs_when_no_custom() -> None:
    """Default calculator should run when no custom hook is provided."""

    usage = {"text_input_tokens": 1000, "text_output_tokens": 2000}
    response = _fake_response(usage)

    @do
    def flow():
        return (
            yield track_api_call(
                operation="generate_content",
                model="gemini-2.5-flash",
                request_summary={"operation": "test"},
                request_payload={"text": "hello"},
                response=response,
                start_time=time.time(),
                error=None,
                api_payload=None,
            )
        )

    engine = ProgramInterpreter()
    ctx = ExecutionContext()
    result = await engine.run_async(flow(), ctx)

    assert result.is_ok
    total_cost = ctx.state.get("gemini_total_cost")
    assert total_cost is not None and total_cost > 0


@pytest.mark.asyncio
async def test_default_cost_calculator_supports_gemini3_image() -> None:
    """Default calculator should handle Gemini 3 Pro Image pricing."""

    usage = {"text_input_tokens": 1_000_000, "text_output_tokens": 0, "image_output_tokens": 0}
    response = _fake_response(usage)

    @do
    def flow():
        return (
            yield track_api_call(
                operation="generate_content",
                model="gemini-3-pro-image-preview",
                request_summary={"operation": "test"},
                request_payload={"text": "hello"},
                response=response,
                start_time=time.time(),
                error=None,
                api_payload=None,
            )
        )

    engine = ProgramInterpreter()
    ctx = ExecutionContext()
    result = await engine.run_async(flow(), ctx)

    assert result.is_ok
    total_cost = ctx.state.get("gemini_total_cost")
    assert total_cost is not None
    # 1M text input tokens at $2 / 1M
    assert total_cost == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_cost_fallback_to_image_tokens_from_total() -> None:
    """If image output tokens are missing, use remaining total tokens for pricing."""

    usage = {
        "text_input_tokens": 27,
        # image_output_tokens intentionally missing
        "total_tokens": 1418,
    }
    response = _fake_response(usage)

    @do
    def flow():
        return (
            yield track_api_call(
                operation="generate_content",
                model="gemini-3-pro-image-preview",
                request_summary={"operation": "test"},
                request_payload={"text": "banana"},
                response=response,
                start_time=time.time(),
                error=None,
                api_payload=None,
            )
        )

    engine = ProgramInterpreter()
    ctx = ExecutionContext()
    result = await engine.run_async(flow(), ctx)

    assert result.is_ok
    total_cost = ctx.state.get("gemini_total_cost")
    assert total_cost is not None
    # Expected: 27 tokens at $2 + 1391 tokens at $120 => ~0.167 USD
    assert total_cost == pytest.approx(0.167, rel=1e-2)


@pytest.mark.asyncio
async def test_custom_cost_calculator_overrides_default() -> None:
    """Injected calculator should override default pricing."""

    usage = {"text_input_tokens": 1000, "text_output_tokens": 2000}
    response = _fake_response(usage)

    @do
    def custom_calculator(call_result: GeminiCallResult) -> EffectGenerator[GeminiCostEstimate]:
        return GeminiCostEstimate(
            cost_info=CostInfo(
                total_cost=1.23,
                text_input_cost=0.1,
                text_output_cost=1.0,
                image_input_cost=0.0,
                image_output_cost=0.13,
            ),
            raw_usage=call_result.payload.get("usage"),
        )

    @do
    def flow():
        return (
            yield Local(
                {"gemini_cost_calculator": custom_calculator},
                track_api_call(
                    operation="generate_content",
                    model="gemini-2.5-flash",
                    request_summary={"operation": "test"},
                    request_payload={"text": "hello"},
                    response=response,
                    start_time=time.time(),
                    error=None,
                    api_payload=None,
                ),
            )
        )

    engine = ProgramInterpreter()
    ctx = ExecutionContext()
    result = await engine.run_async(flow(), ctx)

    assert result.is_ok
    assert ctx.state.get("gemini_total_cost") == pytest.approx(1.23)


@pytest.mark.asyncio
async def test_cost_calculation_failure_raises() -> None:
    """If custom and default calculators fail, the call should error."""

    usage = {"text_input_tokens": 1000, "text_output_tokens": 2000}
    response = _fake_response(usage)

    @do
    def failing_calculator(call_result: GeminiCallResult) -> EffectGenerator[GeminiCostEstimate]:
        _ = call_result
        yield Fail(ValueError("boom"))

    @do
    def flow():
        return (
            yield Local(
                {"gemini_cost_calculator": failing_calculator},
                track_api_call(
                    operation="generate_content",
                    model="unknown-model",
                    request_summary={"operation": "test"},
                    request_payload={"text": "hello"},
                    response=response,
                    start_time=time.time(),
                    error=None,
                    api_payload=None,
                ),
            )
        )

    engine = ProgramInterpreter()
    result = await engine.run_async(flow())

    assert result.is_err
