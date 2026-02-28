"""Integration tests for doeff-openrouter."""


import json
import os
from collections.abc import Callable
from typing import Any

import pytest
from doeff_openrouter.chat import chat_completion
from doeff_openrouter.client import OpenRouterClient
from doeff_openrouter.structured_llm import (
    StructuredOutputParsingError,
    build_messages,
    build_response_format_payload,
    process_structured_response,
)
from pydantic import BaseModel

from doeff import (
    AskEffect,
    Effect,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    default_handlers,
    do,
    run,
)

MOCK_STRUCTURED_MODELS = [
    pytest.param("openai/gpt-4o-mini", True, id="openai-gpt-4o-mini"),
    pytest.param("openai/gpt-5", False, id="openai-gpt-5"),
    pytest.param("google/gemini-2.0-flash-001", True, id="google-gemini-2.0-flash-001"),
    pytest.param("anthropic/claude-3-haiku", False, id="anthropic-claude-3-haiku"),
]
MODELS_WITH_JSON_OUTPUT = {"openai/gpt-4o-mini", "google/gemini-2.0-flash-001"}
LIVE_MODEL = "openai/gpt-4o-mini"

class EchoPayload(BaseModel):
    keyword: str
    number: int


class MockOpenRouterClient:
    """Mocked OpenRouter client that returns canned response payloads."""

    def __init__(self, models_with_json_output: set[str]):
        self.models_with_json_output = models_with_json_output
        self.calls: list[dict[str, Any]] = []

    async def a_chat_completions(
        self,
        request_data: dict[str, Any],
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        model = request_data["model"]
        self.calls.append(
            {
                "request_data": request_data,
                "timeout": timeout,
                "headers": dict(headers or {}),
            }
        )

        if model in self.models_with_json_output:
            content = json.dumps({"keyword": "doeff-openrouter", "number": 17})
        else:
            content = "keyword=doeff-openrouter number=17"

        response_data: dict[str, Any] = {
            "id": "mock-response-id",
            "object": "chat.completion",
            "created": 1739312000,
            "model": model,
            "provider": {"name": "mock-openrouter"},
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "parsed": None,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 9,
                "total_tokens": 21,
            },
        }
        response_headers = {"x-request-id": "mock-request-id"}
        return response_data, response_headers


def _build_mock_handler(client: MockOpenRouterClient) -> Callable[..., Any]:
    @do
    def handler(effect: Effect, k: Any):
        if isinstance(effect, AskEffect) and effect.key == "openrouter_client":
            return (yield Resume(k, client))
        if isinstance(effect, AskEffect) and effect.key == "openrouter_api_key":
            return (yield Resume(k, "fake-key"))
        yield Pass()

    return handler


@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("Set OPENROUTER_API_KEY to run the live OpenRouter e2e smoke test.")
    return key


@pytest.fixture(scope="module")
def handlers() -> tuple[Any, ...]:
    return tuple(default_handlers())


@pytest.mark.parametrize(("model", "expects_success"), MOCK_STRUCTURED_MODELS)
def test_chat_completion_and_structured_response_with_handler_mock(
    model: str,
    expects_success: bool,
    handlers: tuple[Any, ...],
):
    """Structured parsing should work without network calls via handler-based dependency injection."""
    mock_client = MockOpenRouterClient(MODELS_WITH_JSON_OUTPUT)

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

    raw_result = run(
        WithHandler(_build_mock_handler(mock_client), flow()),
        handlers=handlers,
        store={"openrouter_api_calls": []},
    )

    assert raw_result.is_ok(), f"OpenRouter call failed: {raw_result.error}"
    raw = raw_result.value

    assert isinstance(raw, dict)
    assert "choices" in raw, "choices payload missing"
    assert isinstance(raw["choices"], list), "choices payload missing"
    assert raw["choices"], "choices payload missing"
    first_choice = raw["choices"][0]
    assert isinstance(first_choice, dict)
    assert "message" in first_choice
    message = first_choice["message"]
    assert isinstance(message, dict)
    assert isinstance(message.get("content"), str), "OpenRouter message content should be a string"
    assert message.get("parsed") is None

    assert len(mock_client.calls) == 1
    request_data = mock_client.calls[0]["request_data"]
    assert request_data["model"] == model
    assert request_data["response_format"]["type"] == "json_schema"

    @do
    def parse_flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(raw, EchoPayload))

    parse_result = run(parse_flow(), handlers=handlers, store=raw_result.raw_store)

    if expects_success:
        assert parse_result.is_ok(), f"Structured parsing failed: {parse_result.error}"
        structured = parse_result.value
        assert isinstance(structured, EchoPayload)
        assert structured.keyword.lower() == "doeff-openrouter"
        assert structured.number == 17
    else:
        assert parse_result.is_err(), "Expected structured parsing to fail for unsupported model"
        assert isinstance(parse_result.error, StructuredOutputParsingError)


@pytest.mark.e2e
def test_chat_completion_live_smoke(api_key: str, handlers: tuple[Any, ...]) -> None:
    """A minimal live OpenRouter smoke test using the current run()/default_handlers() runtime API."""

    @do
    def flow() -> EffectGenerator[Any]:
        messages = yield build_messages("Reply with a short sentence that includes doeff-openrouter.")
        return (
            yield chat_completion(
                messages=messages,
                model=LIVE_MODEL,
                temperature=0.0,
                max_tokens=120,
            )
        )

    result = run(
        flow(),
        handlers=handlers,
        env={
            "openrouter_api_key": api_key,
            "openrouter_client": OpenRouterClient(api_key=api_key),
        },
        store={"openrouter_api_calls": []},
    )

    assert result.is_ok(), f"OpenRouter call failed: {result.error}"
    response = result.value
    assert isinstance(response, dict)
    assert "choices" in response
    assert isinstance(response["choices"], list)
    assert response["choices"]
