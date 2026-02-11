"""Tests for OpenRouter effect/handler modules."""

from __future__ import annotations

import importlib
import json
from typing import Any

from doeff_openrouter.effects import (
    RouterChat,
    RouterStreamingChat,
    RouterStructuredOutput,
)
from doeff_openrouter.handlers import (
    MockOpenRouterRuntime,
    mock_handlers,
    production_handlers,
)
from pydantic import BaseModel

from doeff import do, run_with_handler_map


class StructuredPayload(BaseModel):
    keyword: str
    number: int


def test_effect_exports():
    from doeff_openrouter.effects import RouterChat as ImportedRouterChat
    from doeff_openrouter.effects import RouterStreamingChat as ImportedRouterStreamingChat
    from doeff_openrouter.effects import RouterStructuredOutput as ImportedRouterStructuredOutput

    assert ImportedRouterChat is RouterChat
    assert ImportedRouterStreamingChat is RouterStreamingChat
    assert ImportedRouterStructuredOutput is RouterStructuredOutput


def test_handler_exports():
    from doeff_openrouter.handlers import mock_handlers as imported_mock_handlers
    from doeff_openrouter.handlers import production_handlers as imported_production_handlers

    assert imported_production_handlers is production_handlers
    assert imported_mock_handlers is mock_handlers


def test_mock_handlers_return_configured_deterministic_payloads() -> None:
    runtime = MockOpenRouterRuntime(
        chat_response={
            "id": "mock-chat-001",
            "choices": [{"message": {"role": "assistant", "content": "mock chat"}}],
        },
        streaming_response={
            "id": "mock-stream-001",
            "choices": [{"delta": {"content": "chunk-1"}, "finish_reason": "stop"}],
        },
        structured_response={"keyword": "mocked", "number": 99},
    )

    @do
    def workflow():
        chat = yield RouterChat(
            messages=[{"role": "user", "content": "hello"}],
            model="openai/gpt-4o-mini",
            temperature=0.0,
        )
        stream = yield RouterStreamingChat(
            messages=[{"role": "user", "content": "stream"}],
            model="openai/gpt-4o-mini",
        )
        structured = yield RouterStructuredOutput(
            messages=[{"role": "user", "content": "return JSON"}],
            response_format=StructuredPayload,
            model="openai/gpt-4o-mini",
        )
        return chat, stream, structured

    result = run_with_handler_map(workflow(), mock_handlers(runtime=runtime))

    assert result.is_ok()
    chat_payload, stream_payload, structured_payload = result.value
    assert chat_payload["id"] == "mock-chat-001"
    assert stream_payload["id"] == "mock-stream-001"
    assert isinstance(structured_payload, StructuredPayload)
    assert structured_payload.keyword == "mocked"
    assert structured_payload.number == 99
    assert [call["effect"] for call in runtime.calls] == [
        "RouterChat",
        "RouterStreamingChat",
        "RouterStructuredOutput",
    ]


def test_handler_swapping_between_mock_and_production(monkeypatch) -> None:
    production_module = importlib.import_module("doeff_openrouter.handlers.production")
    observed_calls: list[dict[str, Any]] = []
    queued_responses = [
        {
            "id": "prod-chat-001",
            "choices": [{"message": {"role": "assistant", "content": "production chat"}}],
        },
        {
            "id": "prod-structured-001",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"keyword": "prod", "number": 17}),
                    }
                }
            ],
        },
    ]

    @do
    def fake_chat_completion(*, messages: list[dict[str, Any]], model: str, **kwargs: Any):
        observed_calls.append(
            {
                "messages": messages,
                "model": model,
                "kwargs": kwargs,
            }
        )
        return queued_responses.pop(0)

    monkeypatch.setattr(production_module, "chat_completion", fake_chat_completion)

    @do
    def workflow():
        chat = yield RouterChat(
            messages=[{"role": "user", "content": "hello prod"}],
            model="openai/gpt-4o-mini",
            temperature=0.3,
        )
        structured = yield RouterStructuredOutput(
            messages=[{"role": "user", "content": "return JSON"}],
            response_format=StructuredPayload,
            model="openai/gpt-4o-mini",
        )
        return chat, structured

    mock_result = run_with_handler_map(
        workflow(),
        mock_handlers(runtime=MockOpenRouterRuntime(chat_response={"id": "mock-only"})),
    )
    production_result = run_with_handler_map(workflow(), production_handlers())

    assert mock_result.is_ok()
    assert production_result.is_ok()

    mock_chat, _ = mock_result.value
    production_chat, production_structured = production_result.value
    assert mock_chat["id"] == "mock-only"
    assert production_chat["id"] == "prod-chat-001"
    assert isinstance(production_structured, StructuredPayload)
    assert production_structured.keyword == "prod"
    assert production_structured.number == 17

    assert len(observed_calls) == 2
    assert observed_calls[0]["kwargs"]["temperature"] == 0.3
    assert observed_calls[1]["kwargs"]["response_format"]["type"] == "json_schema"
