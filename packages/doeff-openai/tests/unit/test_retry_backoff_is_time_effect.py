"""The wait between OpenAI retries is doeff-time's ``Delay``, not a bare ``asyncio.sleep``.

A program yields the time effect and the caller's time handler decides what a second is: the
wall clock in production, a virtual clock in tests. These tests run the retry path on a virtual
clock and read how far it moved: two failed attempts wait 1 s each, so the clock moves 2 s and
the test never sleeps. A program that awaits ``asyncio.sleep`` again leaves the virtual clock
where it was and fails here.
"""

from collections.abc import Callable
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from _runner import run_program
from doeff_openai import structured_llm__openai
from doeff_openai._chat import chat_completion
from doeff_openai._client import OpenAIClient
from doeff_openai._embeddings import create_embedding
from doeff_time import SimClock

from doeff import Ask, EffectGenerator, do


def _chat_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        id="chatcmpl-test",
        model="gpt-4o",
    )


def _embedding_response() -> SimpleNamespace:
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2])],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
        model="text-embedding-3-small",
    )


def _call_chat() -> Any:
    return chat_completion(messages=[{"role": "user", "content": "hi"}], model="gpt-4o")


def _call_embedding() -> Any:
    return create_embedding(input="hi", model="text-embedding-3-small")


def _call_structured() -> Any:
    return structured_llm__openai(text="hi", model="gpt-4o", max_tokens=16, max_retries=3)


@pytest.mark.parametrize(
    ("call", "endpoint", "response"),
    [
        (_call_chat, "chat", _chat_response),
        (_call_embedding, "embeddings", _embedding_response),
        (_call_structured, "chat", _chat_response),
    ],
    ids=["chat_completion", "create_embedding", "structured_llm__openai"],
)
@pytest.mark.asyncio
async def test_retry_waits_on_the_time_handler_clock(
    call: Callable[[], Any], endpoint: str, response: Callable[[], SimpleNamespace]
) -> None:
    client = Mock(spec=OpenAIClient)
    client.async_client = AsyncMock()
    create = (
        client.async_client.chat.completions.create
        if endpoint == "chat"
        else client.async_client.embeddings.create
    )
    create.side_effect = [RuntimeError("transient 1"), RuntimeError("transient 2"), response()]
    clock = SimClock()
    started = clock.current_time

    @do
    def flow() -> EffectGenerator[Any]:
        yield Ask("openai_client")
        return (yield call())

    result = await run_program(flow(), env={"openai_client": client}, clock=clock)

    assert result.is_ok(), result.error
    assert create.await_count == 3
    assert clock.current_time - started == timedelta(seconds=2)
