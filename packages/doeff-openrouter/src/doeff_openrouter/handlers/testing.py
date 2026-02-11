"""Testing handlers for doeff-openrouter effects."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from doeff import Resume
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
    structured_response: Any = field(
        default_factory=lambda: {"keyword": "mocked", "number": 17}
    )
    calls: list[dict[str, Any]] = field(default_factory=list)


def _coerce_structured_payload(effect: RouterStructuredOutput, payload: Any) -> Any:
    if isinstance(payload, effect.response_format):
        return payload
    if isinstance(payload, dict):
        return effect.response_format(**payload)
    return payload


def mock_handlers(
    *,
    runtime: MockOpenRouterRuntime | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Build deterministic in-memory mock handlers for OpenRouter effects."""

    active_runtime = runtime or MockOpenRouterRuntime()

    def handle_chat(effect: RouterChat, k):
        active_runtime.calls.append(
            {
                "effect": "RouterChat",
                "model": effect.model,
                "messages": copy.deepcopy(effect.messages),
            }
        )
        return (yield Resume(k, copy.deepcopy(active_runtime.chat_response)))

    def handle_streaming_chat(effect: RouterStreamingChat, k):
        active_runtime.calls.append(
            {
                "effect": "RouterStreamingChat",
                "model": effect.model,
                "messages": copy.deepcopy(effect.messages),
            }
        )
        return (yield Resume(k, copy.deepcopy(active_runtime.streaming_response)))

    def handle_structured_output(effect: RouterStructuredOutput, k):
        active_runtime.calls.append(
            {
                "effect": "RouterStructuredOutput",
                "model": effect.model,
                "messages": copy.deepcopy(effect.messages),
                "response_format": effect.response_format.__name__,
            }
        )
        payload = _coerce_structured_payload(effect, copy.deepcopy(active_runtime.structured_response))
        return (yield Resume(k, payload))

    return {
        RouterChat: handle_chat,
        RouterStreamingChat: handle_streaming_chat,
        RouterStructuredOutput: handle_structured_output,
    }


__all__ = [
    "MockOpenRouterRuntime",
    "ProtocolHandler",
    "mock_handlers",
]
