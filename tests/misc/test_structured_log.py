"""Tests for the structured logging helpers."""

from collections.abc import Generator
from typing import Any

import pytest

from doeff import Effect, CESKInterpreter, StructuredLog, do, slog


@pytest.mark.asyncio
async def test_slog_emits_dict_entries() -> None:
    """Structured logging should append dict payloads to the writer log."""

    @do
    def program() -> Generator[Effect, Any, None]:
        yield slog(event="start", attempt=1)
        yield StructuredLog(event="progress", attempt=2)
        return None

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.log == [
        {"event": "start", "attempt": 1},
        {"event": "progress", "attempt": 2},
    ]


@pytest.mark.asyncio
async def test_slog_defensive_copy() -> None:
    """Structured logging should copy keyword arguments to avoid external mutation."""

    @do
    def program() -> Generator[Effect, Any, dict]:
        payload = {"event": "captured"}
        yield slog(**payload)
        payload["event"] = "mutated"
        yield StructuredLog(**payload)
        return payload

    engine = CESKInterpreter()
    result = await engine.run_async(program())

    assert result.is_ok
    assert result.log == [
        {"event": "captured"},
        {"event": "mutated"},
    ]
    # Ensure the original payload mutation didn't retroactively change prior log entries.
    assert result.log[0] == {"event": "captured"}
