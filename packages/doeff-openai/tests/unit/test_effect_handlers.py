"""Tests for doeff-openai domain effects and handler maps."""

from __future__ import annotations

from typing import Any

import pytest
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
    production_handlers,
)
from pydantic import BaseModel

from doeff import Await, EffectGenerator, async_run_with_handler_map, do


class StructuredAnswer(BaseModel):
    label: str
    score: int


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


def test_handler_exports() -> None:
    """Public handlers module should expose production and mock handler maps."""
    handler_map = production_handlers()
    assert ChatCompletionEffect in handler_map
    assert StreamingChatCompletion in handler_map
    assert EmbeddingEffect in handler_map
    assert StructuredOutput in handler_map

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
        first_chat = yield ChatCompletionEffect(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-4o-mini",
        )
        second_chat = yield ChatCompletionEffect(
            messages=[{"role": "user", "content": "hello again"}],
            model="gpt-4o-mini",
        )
        stream = yield StreamingChatCompletion(
            messages=[{"role": "user", "content": "stream"}],
            model="gpt-4o-mini",
        )
        stream_text = yield Await(_collect_stream_text(stream))
        embedding = yield EmbeddingEffect(
            input=["alpha", "beta"],
            model="text-embedding-3-small",
        )
        structured = yield StructuredOutput(
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

    result = await async_run_with_handler_map(
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
