"""Unit tests for OpenRouter structured LLM helpers."""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from pydantic import BaseModel

from doeff import EffectGenerator, ExecutionContext, ProgramInterpreter, do
from doeff_openrouter.chat import chat_completion

structured_llm_module = importlib.import_module("doeff_openrouter.structured_llm")
from doeff_openrouter.structured_llm import (
    build_messages,
    ensure_strict_schema,
    process_structured_response,
    process_unstructured_response,
    structured_llm,
)


class DemoModel(BaseModel):
    name: str
    value: int


@pytest.mark.asyncio
async def test_build_messages_text_only():
    """Building messages without images keeps a single user part."""

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield build_messages("hello"))

    engine = ProgramInterpreter()
    result = await engine.run(flow())
    assert result.is_ok
    messages = result.value
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "hello"


def test_ensure_strict_schema_sets_additional_properties():
    """Schemas produced for OpenRouter must disable additionalProperties."""
    schema = {
        "type": "object",
        "properties": {
            "foo": {"type": "string"},
            "bar": {
                "type": "object",
                "properties": {
                    "nested": {"type": "integer"}
                },
            },
        },
    }
    ensure_strict_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["bar"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_process_unstructured_response_flattens_text():
    """Assistant content arrays are flattened into newline separated text."""
    response = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Line 1"},
                        {"type": "text", "text": "Line 2"},
                    ]
                }
            }
        ]
    }
    text = process_unstructured_response(response)
    assert text == "Line 1\nLine 2"


@pytest.mark.asyncio
async def test_process_structured_response_uses_parsed_field():
    """When OpenRouter returns a parsed payload, it is used directly."""
    response = {
        "choices": [
            {
                "message": {
                    "parsed": {"name": "demo", "value": 42},
                }
            }
        ]
    }

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    engine = ProgramInterpreter()
    result = await engine.run(flow())
    assert result.is_ok
    parsed = result.value
    assert isinstance(parsed, DemoModel)
    assert parsed.name == "demo"
    assert parsed.value == 42


@pytest.mark.asyncio
async def test_structured_llm_happy_path(monkeypatch):
    """structured_llm delegates to chat_completion and validates output."""
    payload = {"name": "demo", "value": 7}
    response = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": json.dumps(payload)}
                    ]
                }
            }
        ]
    }

    @do
    def fake_chat_completion(**_: Any) -> EffectGenerator[dict[str, Any]]:
        return response

    monkeypatch.setattr(structured_llm_module, "chat_completion", fake_chat_completion)

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield structured_llm(
            "return payload",
            model="openai/gpt-4o-mini",
            response_format=DemoModel,
        ))

    engine = ProgramInterpreter()
    result = await engine.run(flow())
    assert result.is_ok
    model = result.value
    assert isinstance(model, DemoModel)
    assert model.value == 7


@pytest.mark.asyncio
async def test_structured_llm_without_schema(monkeypatch):
    """When no schema is provided the helper returns plain text."""
    response = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "plain text"}
                    ]
                }
            }
        ]
    }

    @do
    def fake_chat_completion(**_: Any) -> EffectGenerator[dict[str, Any]]:
        return response

    monkeypatch.setattr(structured_llm_module, "chat_completion", fake_chat_completion)

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield structured_llm("plain", model="openrouter/demo"))

    engine = ProgramInterpreter()
    result = await engine.run(flow())
    assert result.is_ok
    assert result.value == "plain text"


@pytest.mark.asyncio
async def test_chat_completion_tracks_prompt_state():
    """chat_completion should record prompt content in state."""

    messages = [{"role": "user", "content": [{"type": "text", "text": "hello router"}]}]
    response_data = {
        "choices": [
            {
                "message": {
                    "content": [{"type": "text", "text": "response"}],
                    "role": "assistant",
                },
                "finish_reason": "stop",
            }
        ],
        "id": "test-id",
    }

    class FakeClient:
        async def a_chat_completions(self, request_data: dict[str, Any], *, timeout=None, headers=None):
            assert request_data["messages"] == messages
            return response_data, {}

    engine = ProgramInterpreter()
    context = ExecutionContext(env={"openrouter_client": FakeClient()})
    result = await engine.run(chat_completion(messages=messages, model="demo-model"), context)

    assert result.is_ok
    assert result.value == response_data

    api_calls = result.context.state.get("openrouter_api_calls")
    assert api_calls is not None
    assert api_calls[0]["prompt_text"] == "hello router"
    assert api_calls[0]["prompt_images"] == []
    assert api_calls[0]["prompt_messages"][0]["content"][0]["text"] == "hello router"
