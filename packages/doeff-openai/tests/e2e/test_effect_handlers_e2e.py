"""E2E smoke tests for doeff-openai domain effect handlers."""

from __future__ import annotations

import os  # noqa: PINJ050 - true e2e env gating

import pytest
from doeff_openai.effects import ChatCompletion as ChatCompletionEffect
from doeff_openai.effects import StructuredOutput
from doeff_openai.handlers import production_handlers
from pydantic import BaseModel

from doeff import EffectGenerator, WithHandler, async_run, default_async_handlers, do

pytestmark = pytest.mark.e2e


class ArithmeticResult(BaseModel):
    value: int


_real_api_key = os.environ.get("OPENAI_API_KEY")
_run_real_e2e = os.environ.get("RUN_OPENAI_E2E") == "1"
_skip_real_e2e = not bool(_real_api_key and _run_real_e2e)


async def _async_run_with_handler(program, handler, *, env):
    return await async_run(
        WithHandler(handler, program),
        handlers=default_async_handlers(),
        env=env,
    )


@pytest.mark.skipif(
    _skip_real_e2e,
    reason="True E2E requires OPENAI_API_KEY and RUN_OPENAI_E2E=1",
)
@pytest.mark.asyncio
async def test_chat_completion_effect_with_production_handler() -> None:
    """Run ChatCompletion effect against the real OpenAI API via production handlers."""

    @do
    def flow() -> EffectGenerator[str]:
        response = yield ChatCompletionEffect(
            messages=[{"role": "user", "content": "Reply with exactly the word doeff."}],
            model="gpt-4o-mini",
            temperature=0.0,
            max_tokens=8,
        )
        return response.choices[0].message.content

    result = await _async_run_with_handler(
        flow(),
        production_handlers(),
        env={"openai_api_key": _real_api_key},
    )

    assert result.is_ok()
    assert isinstance(result.value, str)
    assert "doeff" in result.value.lower()


@pytest.mark.skipif(
    _skip_real_e2e,
    reason="True E2E requires OPENAI_API_KEY and RUN_OPENAI_E2E=1",
)
@pytest.mark.asyncio
async def test_structured_output_effect_with_production_handler() -> None:
    """Run StructuredOutput effect against the real OpenAI API via production handlers."""

    @do
    def flow() -> EffectGenerator[ArithmeticResult]:
        return (
            yield StructuredOutput(
                messages=[
                    {
                        "role": "user",
                        "content": "Return JSON with field value equal to 6 * 7.",
                    }
                ],
                response_format=ArithmeticResult,
                model="gpt-4o-mini",
            )
        )

    result = await _async_run_with_handler(
        flow(),
        production_handlers(),
        env={"openai_api_key": _real_api_key},
    )

    assert result.is_ok()
    assert isinstance(result.value, ArithmeticResult)
    assert result.value.value == 42
