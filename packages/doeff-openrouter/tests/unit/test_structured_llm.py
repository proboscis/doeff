"""Unit tests for OpenRouter structured LLM helpers."""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from doeff_openrouter.chat import chat_completion
from pydantic import BaseModel

from doeff import EffectGenerator, default_handlers, do, run

structured_llm_module = importlib.import_module("doeff_openrouter.structured_llm")
from doeff_openrouter.structured_llm import (
    StructuredOutputParsingError,
    build_messages,
    ensure_strict_schema,
    process_structured_response,
    process_unstructured_response,
    structured_llm,
)


class DemoModel(BaseModel):
    name: str
    value: int


_HANDLERS = tuple(default_handlers())


def run_program(program, *, env: dict[str, Any] | None = None, store: dict[str, Any] | None = None):
    return run(program, handlers=_HANDLERS, env=env, store=store)


def test_build_messages_text_only():
    """Building messages without images keeps a single user part."""

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield build_messages("hello"))

    result = run_program(flow())
    assert result.is_ok()
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


def test_process_unstructured_response_flattens_text():
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


def test_process_structured_response_requires_string_content_even_with_parsed():
    """Even when parsed is present we rely on string content."""
    response = {
        "choices": [
            {
                "message": {
                    "parsed": {"name": "demo", "value": 42},
                    "content": '{"name": "demo", "value": 42}',
                }
            }
        ]
    }

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    result = run_program(flow())
    assert result.is_ok()
    parsed = result.value
    assert isinstance(parsed, DemoModel)
    assert parsed.name == "demo"
    assert parsed.value == 42


def test_process_structured_response_errors_when_content_missing():
    """Parsed alone is insufficient without string content."""
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

    result = run_program(flow())
    assert result.is_err()
    error = result.error
    assert isinstance(error, RuntimeError)
    assert "content" in str(error).lower()


def test_process_structured_response_parses_json_string_content():
    """When the API returns JSON in a string we parse it directly."""
    payload = {"name": "fallback", "value": 11}
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(payload),
                }
            }
        ]
    }

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    result = run_program(flow())
    assert result.is_ok()
    parsed = result.value
    assert isinstance(parsed, DemoModel)
    assert parsed.name == "fallback"
    assert parsed.value == 11


def test_process_structured_response_parses_code_fence_content():
    """Markdown fences around JSON are stripped before parsing."""
    payload = {"name": "textual", "value": 13}
    response = {
        "choices": [
            {
                "message": {
                    "content": f"```json\n{json.dumps(payload)}\n```",
                }
            }
        ]
    }

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    result = run_program(flow())
    assert result.is_ok()
    parsed = result.value
    assert isinstance(parsed, DemoModel)
    assert parsed.name == "textual"
    assert parsed.value == 13


def test_process_structured_response_errors_when_content_not_json():
    """Plain string content that is not JSON raises a parsing error."""
    response = {
        "choices": [
            {
                "message": {
                    "content": "not json",
                }
            }
        ]
    }

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    result = run_program(flow())
    assert result.is_err()
    error = result.error
    assert isinstance(error, StructuredOutputParsingError)


def test_process_structured_response_errors_when_choices_missing():
    """Responses without at least one choice are treated as malformed."""
    response = {"choices": []}

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    result = run_program(flow())
    assert result.is_err()
    error = result.error
    assert isinstance(error, RuntimeError)
    assert "choices" in str(error).lower()


def test_process_structured_response_errors_when_content_not_string():
    """Content must be a string when parsed data is absent."""
    response = {
        "choices": [
            {
                "message": {
                    "content": {"type": "text", "text": '{"name": "bad", "value": 0}'},
                }
            }
        ]
    }

    @do
    def flow() -> EffectGenerator[Any]:
        return (yield process_structured_response(response, DemoModel))

    result = run_program(flow())
    assert result.is_err()
    error = result.error
    assert isinstance(error, RuntimeError)
    assert "content" in str(error).lower()


def test_structured_llm_happy_path(monkeypatch):
    """structured_llm delegates to chat_completion and validates output."""
    payload = {"name": "demo", "value": 7}
    response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(payload),
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

    result = run_program(flow())
    assert result.is_ok()
    model = result.value
    assert isinstance(model, DemoModel)
    assert model.value == 7


def test_structured_llm_without_schema(monkeypatch):
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

    result = run_program(flow())
    assert result.is_ok()
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

    result = run(
        chat_completion(messages=messages, model="demo-model"),
        handlers=_HANDLERS,
        env={"openrouter_client": FakeClient()},
        store={"openrouter_api_calls": []},
    )

    assert result.is_ok()
    assert result.value == response_data

    api_calls = result.raw_store.get("openrouter_api_calls")
    assert api_calls is not None
    assert api_calls[0]["prompt_text"] == "hello router"
    assert api_calls[0]["prompt_images"] == []
    assert api_calls[0]["prompt_messages"][0]["content"][0]["text"] == "hello router"
