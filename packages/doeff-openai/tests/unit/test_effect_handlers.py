"""Tests for doeff-openai domain effects and handler maps."""


from typing import Any

import pytest
from doeff_llm.effects import (
    LLMChat,
    LLMEmbedding,
    LLMStreamingChat,
    LLMStructuredQuery,
)
from doeff_openai.effects import (
    ChatCompletion as ChatCompletionEffect,
)
from doeff_openai.effects import (
    Embedding as EmbeddingEffect,
)
from doeff_openai.effects import (
    StreamingChatCompletion,
    StructuredOutput,
)
from doeff_openai.handlers import (
    MockOpenAIConfig,
    MockOpenAIState,
    mock_handlers,
    openai_mock_handler,
    production_handlers,
)
from pydantic import BaseModel

from doeff import (
    Await,
    Delegate,
    EffectGenerator,
    Resume,
    WithHandler,
    async_run,
    default_async_handlers,
    default_handlers,
    do,
)
from doeff.effects.base import Effect


class StructuredAnswer(BaseModel):
    label: str
    score: int


async def _async_run_with_handler(program, handler):
    return await async_run(
        WithHandler(handler, program),
        handlers=default_async_handlers(),
    )


async def _collect_stream_text(stream: Any) -> str:
    pieces: list[str] = []
    async for chunk in stream:
        choice = chunk.choices[0]
        text = getattr(choice.delta, "content", None)
        if text:
            pieces.append(text)
    return "".join(pieces).strip()


def test_effect_exports() -> None:
    """Public effects module should expose all required domain effects."""
    assert ChatCompletionEffect.__name__ == "ChatCompletion"
    assert StreamingChatCompletion.__name__ == "StreamingChatCompletion"
    assert EmbeddingEffect.__name__ == "Embedding"
    assert StructuredOutput.__name__ == "StructuredOutput"
    assert issubclass(ChatCompletionEffect, LLMChat)
    assert issubclass(StreamingChatCompletion, LLMStreamingChat)
    assert issubclass(EmbeddingEffect, LLMEmbedding)
    assert issubclass(StructuredOutput, LLMStructuredQuery)


def test_deprecated_effect_aliases_emit_warnings() -> None:
    with pytest.deprecated_call(match="ChatCompletion is deprecated"):
        ChatCompletionEffect(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o-mini",
        )
    with pytest.deprecated_call(match="StreamingChatCompletion is deprecated"):
        StreamingChatCompletion(
            messages=[{"role": "user", "content": "stream"}],
            model="gpt-4o-mini",
        )
    with pytest.deprecated_call(match="Embedding is deprecated"):
        EmbeddingEffect(
            input="hello",
            model="text-embedding-3-small",
        )
    with pytest.deprecated_call(match="StructuredOutput is deprecated"):
        StructuredOutput(
            messages=[{"role": "user", "content": "json"}],
            response_format=StructuredAnswer,
            model="gpt-4o-mini",
        )


def test_handler_exports() -> None:
    """Public handlers module should expose callable protocol handlers."""
    assert callable(production_handlers())
    assert callable(mock_handlers)


@pytest.mark.asyncio
async def test_mock_handlers_support_handler_swapping_for_all_effects() -> None:
    """Mock handler map should provide deterministic responses for all domain effects."""
    config = MockOpenAIConfig(
        chat_responses=["first mock reply", "second mock reply"],
        streaming_responses=["stream one two"],
        embedding_vectors=[
            [[0.1, 0.2, 0.3], [0.9, 0.8, 0.7]],
        ],
        structured_responses=[
            {"label": "mock-structured", "score": 7},
        ],
    )
    state = MockOpenAIState()

    @do
    def flow() -> EffectGenerator[dict[str, Any]]:
        first_chat = yield LLMChat(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o-mini",
        )
        second_chat = yield LLMChat(
            messages=[{"role": "user", "content": "hello again"}],
            model="gpt-4o-mini",
        )
        stream = yield LLMStreamingChat(
            messages=[{"role": "user", "content": "stream"}],
            model="gpt-4o-mini",
        )
        stream_text = yield Await(_collect_stream_text(stream))
        embedding = yield LLMEmbedding(
            input=["alpha", "beta"],
            model="text-embedding-3-small",
        )
        structured = yield LLMStructuredQuery(
            messages=[{"role": "user", "content": "Return structured output"}],
            response_format=StructuredAnswer,
            model="gpt-4o-mini",
        )
        return {
            "first_chat": first_chat,
            "second_chat": second_chat,
            "stream_text": stream_text,
            "embedding": embedding,
            "structured": structured,
        }

    result = await _async_run_with_handler(
        flow(),
        mock_handlers(config=config, state=state),
    )

    assert result.is_ok()

    assert result.value["first_chat"].choices[0].message.content == "first mock reply"
    assert result.value["second_chat"].choices[0].message.content == "second mock reply"
    assert result.value["stream_text"] == "stream one two"

    embedding_data = result.value["embedding"].data
    assert len(embedding_data) == 2
    assert embedding_data[0].embedding == [0.1, 0.2, 0.3]
    assert embedding_data[1].embedding == [0.9, 0.8, 0.7]

    structured = result.value["structured"]
    assert isinstance(structured, StructuredAnswer)
    assert structured.label == "mock-structured"
    assert structured.score == 7

    assert state.chat_calls == 2
    assert state.streaming_calls == 1
    assert state.embedding_calls == 1
    assert state.structured_calls == 1
    assert len(state.calls) == 5


@pytest.mark.asyncio
async def test_openai_handler_delegates_unsupported_models() -> None:
    config = MockOpenAIConfig(chat_responses=["openai-response"])
    state = MockOpenAIState()

    @do
    def wrapped_openai_handler(effect: Effect, k: Any):
        return (
            yield openai_mock_handler(
                effect,
                k,
                config=config,
                state=state,
            )
        )

    @do
    def fallback_handler(effect: Effect, k: Any):
        if isinstance(effect, LLMChat):
            return (yield Resume(k, "fallback-response"))
        yield Delegate()

    @do
    def flow() -> EffectGenerator[str]:
        return (
            yield LLMChat(
                messages=[{"role": "user", "content": "delegate to fallback"}],
                model="gemini-1.5-pro",
            )
        )

    result = await async_run(
        WithHandler(fallback_handler, WithHandler(wrapped_openai_handler, flow())),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == "fallback-response"
    assert state.chat_calls == 0
