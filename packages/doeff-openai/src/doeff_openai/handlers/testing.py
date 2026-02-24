"""Deterministic mock handlers for doeff-openai domain effects."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypeVar, get_args, get_origin

from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredQuery,
)

from doeff import Pass, Resume
from doeff_openai.effects import (
    ChatCompletion,
    Embedding,
    StreamingChatCompletion,
    StructuredOutput,
)
from doeff_openai.handlers.production import _is_openai_model

ProtocolHandler = Callable[[Any, Any], Any]

T = TypeVar("T")
ConfiguredValue = Any


@dataclass
class MockOpenAIConfig:
    """Configurable deterministic responses for mock effect handlers."""

    chat_responses: list[str] = field(default_factory=lambda: ["mock"])
    streaming_responses: list[str] = field(default_factory=lambda: ["mock stream"])
    embedding_vectors: list[list[float] | list[list[float]]] = field(
        default_factory=lambda: [[0.0, 0.0, 0.0]]
    )
    structured_responses: list[ConfiguredValue] = field(default_factory=lambda: [{"value": "mock"}])


@dataclass
class MockOpenAIState:
    """In-memory state for deterministic mock effect handling."""

    chat_calls: int = 0
    streaming_calls: int = 0
    embedding_calls: int = 0
    structured_calls: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)


def _pick(values: Sequence[T], index: int, default: T) -> T:
    if not values:
        return default
    if index < len(values):
        return values[index]
    return values[-1]


def _resolve_configured_value(value: ConfiguredValue, effect: Any, call_index: int) -> Any:
    if not callable(value):
        return value
    try:
        return value(effect, call_index)
    except TypeError:
        try:
            return value(effect)
        except TypeError:
            return value()


def _word_count(text: str) -> int:
    return max(len(text.split()), 1)


def _estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += _word_count(content)
            continue

        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        total += _word_count(text)

    return max(total, 1)


def _build_chat_response(content: str, model: str, call_index: int, prompt_tokens: int) -> Any:
    completion_tokens = _word_count(content)
    total_tokens = prompt_tokens + completion_tokens
    return SimpleNamespace(
        id=f"chatcmpl-mock-{call_index}",
        model=model,
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason="stop",
                message=SimpleNamespace(
                    role="assistant",
                    content=content,
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def _build_stream(content: str, model: str, call_index: int) -> AsyncIterator[Any]:
    pieces = content.split()
    if not pieces:
        pieces = [""]

    async def _iterate() -> AsyncIterator[Any]:
        for idx, piece in enumerate(pieces):
            suffix = "" if idx == len(pieces) - 1 else " "
            yield SimpleNamespace(
                id=f"chatcmpl-stream-mock-{call_index}",
                model=model,
                choices=[
                    SimpleNamespace(
                        index=0,
                        finish_reason="stop" if idx == len(pieces) - 1 else None,
                        delta=SimpleNamespace(
                            role="assistant" if idx == 0 else None,
                            content=f"{piece}{suffix}",
                        ),
                    )
                ],
            )

    return _iterate()


def _normalize_vectors(
    configured: list[float] | list[list[float]],
    input_count: int,
) -> list[list[float]]:
    if not configured:
        return [[0.0, 0.0, 0.0] for _ in range(input_count)]

    first = configured[0]
    if isinstance(first, list):
        vectors = [list(vector) for vector in configured if isinstance(vector, list)]
        if not vectors:
            vectors = [[0.0, 0.0, 0.0]]
    else:
        vectors = [list(configured)]

    if len(vectors) >= input_count:
        return vectors[:input_count]

    last = vectors[-1]
    missing = input_count - len(vectors)
    return [*vectors, *[list(last) for _ in range(missing)]]


def _build_embedding_response(
    vectors: list[list[float]],
    model: str,
    call_index: int,
    prompt_tokens: int,
) -> Any:
    return SimpleNamespace(
        id=f"embd-mock-{call_index}",
        object="list",
        model=model,
        data=[
            SimpleNamespace(
                object="embedding",
                index=i,
                embedding=vector,
            )
            for i, vector in enumerate(vectors)
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            total_tokens=prompt_tokens,
        ),
    )


def _default_value_for_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        scalar_defaults = {
            str: "mock",
            int: 0,
            float: 0.0,
            bool: False,
        }
        if annotation in scalar_defaults:
            default_value = scalar_defaults[annotation]
        elif annotation is dict:
            default_value = {}
        elif annotation is list:
            default_value = []
        else:
            default_value = None
        return default_value

    if origin is list:
        return []
    if origin is dict:
        return {}

    args = [arg for arg in get_args(annotation) if arg is not type(None)]
    next_annotation = args[0] if args else None
    return _default_value_for_annotation(next_annotation) if next_annotation is not None else None


def _default_structured_payload(response_format: type[Any]) -> dict[str, Any]:
    annotations = getattr(response_format, "__annotations__", {})
    if not annotations:
        return {}
    return {
        key: _default_value_for_annotation(annotation) for key, annotation in annotations.items()
    }


def _coerce_structured_value(payload: Any, response_format: type[Any]) -> Any:
    if isinstance(payload, response_format):
        return payload

    if hasattr(response_format, "model_validate"):
        if isinstance(payload, dict):
            return response_format.model_validate(payload)  # type: ignore[attr-defined]
        raise TypeError(
            f"Mock structured payload must be a dict for {response_format.__name__}: {payload!r}"
        )

    if isinstance(payload, dict):
        return response_format(**payload)
    return payload


def _handle_chat_completion(
    effect: LLMChat,
    k: Any,
    *,
    config: MockOpenAIConfig,
    state: MockOpenAIState,
):
    call_index = state.chat_calls
    state.chat_calls += 1

    content = _pick(config.chat_responses, call_index, "mock")
    prompt_tokens = _estimate_prompt_tokens(effect.messages)
    response = _build_chat_response(content, effect.model, call_index, prompt_tokens)

    state.calls.append(
        {
            "effect": "ChatCompletion",
            "model": effect.model,
            "messages": effect.messages,
            "response_content": content,
        }
    )
    return (yield Resume(k, response))


def _handle_streaming_chat_completion(
    effect: LLMStreamingChat | LLMChat,
    k: Any,
    *,
    config: MockOpenAIConfig,
    state: MockOpenAIState,
):
    call_index = state.streaming_calls
    state.streaming_calls += 1

    content = _pick(config.streaming_responses, call_index, "mock stream")
    stream = _build_stream(content, effect.model, call_index)

    state.calls.append(
        {
            "effect": "StreamingChatCompletion",
            "model": effect.model,
            "messages": effect.messages,
            "response_content": content,
        }
    )
    return (yield Resume(k, stream))


def _handle_embedding(
    effect: LLMEmbedding,
    k: Any,
    *,
    config: MockOpenAIConfig,
    state: MockOpenAIState,
):
    call_index = state.embedding_calls
    state.embedding_calls += 1

    if isinstance(effect.input, list):
        input_count = len(effect.input)
        prompt_tokens = sum(_word_count(item) for item in effect.input)
    else:
        input_count = 1
        prompt_tokens = _word_count(effect.input)

    configured_vectors = _pick(config.embedding_vectors, call_index, [0.0, 0.0, 0.0])
    vectors = _normalize_vectors(configured_vectors, input_count)
    response = _build_embedding_response(vectors, effect.model, call_index, prompt_tokens)

    state.calls.append(
        {
            "effect": "Embedding",
            "model": effect.model,
            "input": effect.input,
            "vectors": vectors,
        }
    )
    return (yield Resume(k, response))


def _handle_structured_output(
    effect: LLMStructuredQuery,
    k: Any,
    *,
    config: MockOpenAIConfig,
    state: MockOpenAIState,
):
    call_index = state.structured_calls
    state.structured_calls += 1

    configured_payload = _pick(
        config.structured_responses,
        call_index,
        _default_structured_payload(effect.response_format),
    )
    payload = _resolve_configured_value(configured_payload, effect, call_index)
    value = _coerce_structured_value(payload, effect.response_format)

    state.calls.append(
        {
            "effect": "StructuredOutput",
            "model": effect.model,
            "messages": effect.messages,
            "payload": payload,
        }
    )
    return (yield Resume(k, value))


def openai_mock_handler(
    effect: Any,
    k: Any,
    *,
    config: MockOpenAIConfig | None = None,
    state: MockOpenAIState | None = None,
):
    """Single protocol handler suitable for ``WithHandler`` usage."""
    resolved_config = config or MockOpenAIConfig()
    resolved_state = state or MockOpenAIState()

    if isinstance(effect, LLMStreamingChat | StreamingChatCompletion):
        if _is_openai_model(effect.model):
            return (
                yield from _handle_streaming_chat_completion(
                    effect, k, config=resolved_config, state=resolved_state
                )
            )
    elif isinstance(effect, LLMChat | ChatCompletion):
        if _is_openai_model(effect.model):
            if effect.stream:
                return (
                    yield from _handle_streaming_chat_completion(
                        effect, k, config=resolved_config, state=resolved_state
                    )
                )
            return (
                yield from _handle_chat_completion(
                    effect, k, config=resolved_config, state=resolved_state
                )
            )
    elif isinstance(effect, LLMEmbedding | Embedding) and _is_openai_model(effect.model):
        return (
            yield from _handle_embedding(effect, k, config=resolved_config, state=resolved_state)
        )
    elif isinstance(effect, LLMStructuredQuery | StructuredOutput) and _is_openai_model(
        effect.model
    ):
        return (
            yield from _handle_structured_output(
                effect, k, config=resolved_config, state=resolved_state
            )
        )
    yield Pass()


def mock_handlers(
    *,
    config: MockOpenAIConfig | None = None,
    state: MockOpenAIState | None = None,
) -> dict[type[Any], ProtocolHandler]:
    """Typed handler map for deterministic local tests."""
    resolved_config = config or MockOpenAIConfig()
    resolved_state = state or MockOpenAIState()

    def handle_chat(effect: ChatCompletion, k: Any):
        if not _is_openai_model(effect.model):
            yield Pass()
            return
        if effect.stream:
            return (
                yield from _handle_streaming_chat_completion(
                    effect, k, config=resolved_config, state=resolved_state
                )
            )
        return (
            yield from _handle_chat_completion(
                effect, k, config=resolved_config, state=resolved_state
            )
        )

    def handle_stream(effect: LLMStreamingChat | StreamingChatCompletion, k: Any):
        if not _is_openai_model(effect.model):
            yield Pass()
            return
        return (
            yield from _handle_streaming_chat_completion(
                effect, k, config=resolved_config, state=resolved_state
            )
        )

    def handle_embedding(effect: LLMEmbedding | Embedding, k: Any):
        if not _is_openai_model(effect.model):
            yield Pass()
            return
        return (
            yield from _handle_embedding(effect, k, config=resolved_config, state=resolved_state)
        )

    def handle_structured(effect: LLMStructuredQuery | StructuredOutput, k: Any):
        if not _is_openai_model(effect.model):
            yield Pass()
            return
        return (
            yield from _handle_structured_output(
                effect, k, config=resolved_config, state=resolved_state
            )
        )

    return {
        ChatCompletion: handle_chat,
        StreamingChatCompletion: handle_stream,
        Embedding: handle_embedding,
        StructuredOutput: handle_structured,
        LLMChat: handle_chat,
        LLMStreamingChat: handle_stream,
        LLMEmbedding: handle_embedding,
        LLMStructuredQuery: handle_structured,
    }


__all__ = [
    "MockOpenAIConfig",
    "MockOpenAIState",
    "ProtocolHandler",
    "mock_handlers",
    "openai_mock_handler",
]
