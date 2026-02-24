"""Testing handlers for doeff-openrouter effects."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredOutput,
)

from doeff import Pass, Resume
from doeff_openrouter.effects import (
    RouterChat,
    RouterStreamingChat,
    RouterStructuredOutput,
)

ProtocolHandler = Callable[[Any, Any], Any]


def _default_chat_response() -> dict[str, Any]:
    return {
        "id": "mock-router-chat",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "mock chat response",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def _default_streaming_response() -> dict[str, Any]:
    return {
        "id": "mock-router-stream",
        "choices": [
            {
                "index": 0,
                "delta": {"content": "mock stream token"},
                "finish_reason": "stop",
            }
        ],
    }


@dataclass
class MockOpenRouterRuntime:
    """In-memory deterministic payloads used by mock handlers."""

    chat_response: dict[str, Any] = field(default_factory=_default_chat_response)
    streaming_response: dict[str, Any] = field(default_factory=_default_streaming_response)
    structured_response: Any = field(default_factory=lambda: {"keyword": "mocked", "number": 17})
    calls: list[dict[str, Any]] = field(default_factory=list)


def _coerce_structured_payload(effect: LLMStructuredOutput, payload: Any) -> Any:
    if isinstance(payload, effect.response_format):
        return payload
    if isinstance(payload, dict):
        return effect.response_format(**payload)
    return payload


def mock_handlers(
    *,
    runtime: MockOpenRouterRuntime | None = None,
) -> ProtocolHandler:
    """Build a deterministic in-memory protocol handler for OpenRouter effects."""

    active_runtime = runtime or MockOpenRouterRuntime()

    def handler(effect: Any, k: Any):
        return (yield from openrouter_mock_handler(effect, k, runtime=active_runtime))

    return handler


def openrouter_mock_handler(
    effect: Any,
    k: Any,
    *,
    runtime: MockOpenRouterRuntime | None = None,
):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    active_runtime = runtime or MockOpenRouterRuntime()

    if isinstance(effect, LLMStreamingChat | RouterStreamingChat):
        active_runtime.calls.append(
            {
                "effect": effect.__class__.__name__,
                "model": effect.model,
                "messages": copy.deepcopy(effect.messages),
            }
        )
        return (yield Resume(k, copy.deepcopy(active_runtime.streaming_response)))
    if isinstance(effect, LLMChat | RouterChat):
        active_runtime.calls.append(
            {
                "effect": effect.__class__.__name__,
                "model": effect.model,
                "messages": copy.deepcopy(effect.messages),
            }
        )
        if effect.stream:
            return (yield Resume(k, copy.deepcopy(active_runtime.streaming_response)))
        return (yield Resume(k, copy.deepcopy(active_runtime.chat_response)))
    if isinstance(effect, LLMStructuredOutput | RouterStructuredOutput):
        active_runtime.calls.append(
            {
                "effect": effect.__class__.__name__,
                "model": effect.model,
                "messages": copy.deepcopy(effect.messages),
                "response_format": effect.response_format.__name__,
            }
        )
        payload = _coerce_structured_payload(
            effect, copy.deepcopy(active_runtime.structured_response)
        )
        return (yield Resume(k, payload))
    if isinstance(effect, LLMEmbedding):
        yield Pass()
        return
    yield Pass()


__all__ = [
    "MockOpenRouterRuntime",
    "ProtocolHandler",
    "mock_handlers",
    "openrouter_mock_handler",
]
