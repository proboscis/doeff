"""E2E tests that exercise the real OpenRouter API."""

from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic import BaseModel

from doeff import EffectGenerator, ExecutionContext, ProgramInterpreter, do
from doeff_openrouter.chat import chat_completion
from doeff_openrouter.structured_llm import (
    build_messages,
    build_response_format_payload,
    process_structured_response,
    StructuredOutputParsingError,
)

STRUCTURED_MODELS = [
    pytest.param("openai/gpt-4o-mini", True, id="openai-gpt-4o-mini"),
    pytest.param("openai/gpt-5", False, id="openai-gpt-5"),
    pytest.param("google/gemini-2.0-flash-001", True, id="google-gemini-2.0-flash-001"),
    pytest.param("anthropic/claude-3-haiku", False, id="anthropic-claude-3-haiku"),
]

pytestmark = pytest.mark.e2e


class EchoPayload(BaseModel):
    keyword: str
    number: int


@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("Set OPENROUTER_API_KEY to run OpenRouter e2e tests.")
    return key


@pytest.fixture(scope="module")
def engine() -> ProgramInterpreter:
    return ProgramInterpreter()


def _make_context(api_key: str) -> ExecutionContext:
    return ExecutionContext(env={"openrouter_api_key": api_key})


@pytest.mark.parametrize("model, expects_success", STRUCTURED_MODELS)
def test_chat_completion_and_structured_response_live(model: str, expects_success: bool, api_key: str, engine: ProgramInterpreter):
    """Ensure the real OpenRouter API returns the documented structure for supported providers."""
    context = _make_context(api_key)

    @do
    def flow() -> EffectGenerator[Any]:
        messages = yield build_messages(
            "Return the exact keyword 'doeff-openrouter' and number 17 as specified.",
        )
        response_format_payload = build_response_format_payload(EchoPayload)
        raw_response = yield chat_completion(
            messages=messages,
            model=model,
            response_format=response_format_payload,
            temperature=0.0,
            max_tokens=200,
        )
        return raw_response

    raw_result = engine.run(flow(), context)

    assert raw_result.is_ok, f"OpenRouter call failed: {raw_result.result.error}"
    raw = raw_result.value

    assert isinstance(raw, dict)
    assert "choices" in raw and isinstance(raw["choices"], list) and raw["choices"], "choices payload missing"
    first_choice = raw["choices"][0]
    assert isinstance(first_choice, dict) and "message" in first_choice
    message = first_choice["message"]
    assert isinstance(message, dict)
    assert isinstance(message.get("content"), str), "OpenRouter message content should be a string"
    assert message.get("parsed") is None

    @do
    def parse_flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(raw, EchoPayload))

    parse_result = engine.run(parse_flow(), raw_result.context)

    if expects_success:
        assert parse_result.is_ok, f"Structured parsing failed: {parse_result.result.error}"
        structured = parse_result.value
        assert isinstance(structured, EchoPayload)
        assert structured.keyword.lower() == "doeff-openrouter"
        assert structured.number == 17
    else:
        assert parse_result.is_err, "Expected structured parsing to fail for unsupported model"
        error = parse_result.result.error
        assert isinstance(error, (StructuredOutputParsingError, RuntimeError))
