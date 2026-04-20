"""E2E smoke tests for doeff-openai domain effect handlers."""


import os  # noqa: PINJ050 — only for the RUN_OPENAI_E2E gate; the API key itself flows through a handler below

import pytest
from doeff_openai.effects import ChatCompletion as ChatCompletionEffect
from doeff_openai.effects import StructuredOutput
from doeff_openai.handlers import production_handlers
from pydantic import BaseModel

from doeff import EffectGenerator, WithHandler, do

from _runner import (
    doeff_py_has_openai_key,
    openai_api_key_from_doeff_py_handler,
    run_program,
)

pytestmark = pytest.mark.e2e


class ArithmeticResult(BaseModel):
    value: int


_run_real_e2e = os.environ.get("RUN_OPENAI_E2E") == "1"
_skip_real_e2e = not (_run_real_e2e and doeff_py_has_openai_key())


async def _async_run_with_handler(program, handler):
    """Run with the real OpenAI production handler + doeff.py key resolver.

    No ``env=`` dict — ``Ask("openai_api_key")`` is resolved by
    ``openai_api_key_from_doeff_py_handler`` reading
    ``openai_api_key__personal`` from ``~/.doeff.py``. That follows the
    project convention of keeping secrets in ``~/.doeff.py`` rather than
    environment variables.
    """
    return await run_program(
        WithHandler(
            openai_api_key_from_doeff_py_handler, WithHandler(handler, program)
        )
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

    result = await _async_run_with_handler(flow(), production_handlers())

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

    result = await _async_run_with_handler(flow(), production_handlers())

    assert result.is_ok()
    assert isinstance(result.value, ArithmeticResult)
    assert result.value.value == 42
