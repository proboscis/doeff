"""E2E smoke tests for doeff-openai domain effect handlers."""



import pytest
from _runner import (
    openai_api_key_from_doeff_py_handler,
    run_program,
)
from doeff_openai.effects import ChatCompletion as ChatCompletionEffect
from doeff_openai.effects import StructuredOutput
from doeff_openai.handlers import production_handlers
from pydantic import BaseModel

from doeff import EffectGenerator, do

pytestmark = pytest.mark.e2e


class ArithmeticResult(BaseModel):
    value: int




async def _async_run_with_handler(program, handler):
    """Run with the real OpenAI production handler + doeff.py key resolver.

    No ``env=`` dict — ``Ask("openai_api_key")`` is resolved by
    ``openai_api_key_from_doeff_py_handler`` reading
    ``openai_api_key__personal`` from ``~/.doeff.py``. That follows the
    project convention of keeping secrets in ``~/.doeff.py`` rather than
    environment variables.
    """
    return await run_program(
        openai_api_key_from_doeff_py_handler(handler(program))
    )


@pytest.mark.real_openai
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


@pytest.mark.real_openai
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
