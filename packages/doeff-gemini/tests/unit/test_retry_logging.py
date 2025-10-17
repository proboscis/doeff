"""Retry logging tests that avoid the google.genai dependency."""

from __future__ import annotations

import asyncio
import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

if "pydantic" not in sys.modules:
    import types

    pydantic_stub = types.ModuleType("pydantic")

    class BaseModel:  # type: ignore[no-redef]
        """Lightweight stub used for tests."""

        def model_dump(self) -> dict[str, Any]:
            return {}

    class ValidationError(Exception):  # type: ignore[no-redef]
        """Stub ValidationError."""

    pydantic_stub.BaseModel = BaseModel  # type: ignore[attr-defined]
    pydantic_stub.ValidationError = ValidationError  # type: ignore[attr-defined]
    sys.modules["pydantic"] = pydantic_stub

from doeff import EffectGenerator, ExecutionContext, ProgramInterpreter, do
from doeff_gemini.structured_llm import edit_image__gemini, structured_llm__gemini

structured_llm_module = importlib.import_module("doeff_gemini.structured_llm")


@do
def _fake_build_contents(*args: Any, **kwargs: Any) -> EffectGenerator[list[Any]]:
    """Return a simple contents payload for testing."""
    text = kwargs.get("text") or (args[0] if args else "")
    return [{"role": "user", "parts": [{"text": text}]}]


@do
def _fake_build_generation_config(**kwargs: Any) -> EffectGenerator[Any]:
    """Return a lightweight config namespace for downstream mocks."""
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_structured_llm_retry_failure_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry exhaustion should emit an error-level structured log and propagate the error."""

    monkeypatch.setattr(structured_llm_module, "build_contents", _fake_build_contents)
    monkeypatch.setattr(
        structured_llm_module,
        "build_generation_config",
        _fake_build_generation_config,
    )
    monkeypatch.setattr(
        structured_llm_module,
        "_gemini_random_backoff",
        lambda attempt, error: 0.0,
    )

    assert hasattr(structured_llm_module, "slog")
    assert structured_llm_module.slog is not None
    monkeypatch.setattr(builtins, "slog", structured_llm_module.slog, raising=False)
    monkeypatch.setattr(builtins, "slog", structured_llm_module.slog, raising=False)

    async_models = MagicMock()
    async_models.generate_content = AsyncMock(
        side_effect=[
            RuntimeError("Attempt 1 failed"),
            RuntimeError("Resource exhausted"),
        ]
    )
    async_client = MagicMock()
    async_client.models = async_models
    mock_client = MagicMock()
    mock_client.async_client = async_client

    # Avoid accidentally awaiting the real asyncio sleep if the strategy changes.
    async def fake_sleep(_duration: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    @do
    def flow() -> EffectGenerator[str]:
        return (
            yield structured_llm__gemini(
                text="Hello Gemini",
                model="gemini-1.5-flash",
                max_output_tokens=128,
                max_retries=2,
            )
        )

    engine = ProgramInterpreter()
    ctx = ExecutionContext(env={"gemini_client": mock_client})
    result = await engine.run_async(flow(), ctx)

    assert result.is_err
    error = result.result.error
    while hasattr(error, "cause") and getattr(error, "cause") is not None:
        error = error.cause
    assert isinstance(error, RuntimeError)
    assert str(error) == "Resource exhausted"

    structured_logs = [entry for entry in result.log if isinstance(entry, dict)]
    failure_logs = [
        entry for entry in structured_logs if entry.get("event") == "gemini.retry_exhausted"
    ]
    assert failure_logs, "Expected retry exhaustion log entry"
    failure_entry = failure_logs[-1]
    assert failure_entry["level"] == "ERROR"
    assert failure_entry["attempts"] == 2
    assert failure_entry["model"] == "gemini-1.5-flash"
    assert "Resource exhausted" in failure_entry["error"]


@pytest.mark.asyncio
async def test_edit_image_retry_failure_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Image edit retries should log and propagate the final error."""

    monkeypatch.setattr(structured_llm_module, "build_contents", _fake_build_contents)
    monkeypatch.setattr(
        structured_llm_module,
        "build_generation_config",
        _fake_build_generation_config,
    )
    monkeypatch.setattr(
        structured_llm_module,
        "_gemini_random_backoff",
        lambda attempt, error: 0.0,
    )

    assert hasattr(structured_llm_module, "slog")
    monkeypatch.setattr(builtins, "slog", structured_llm_module.slog, raising=False)

    async_models = MagicMock()
    async_models.generate_content = AsyncMock(
        side_effect=[
            RuntimeError("Attempt 1 failed"),
            RuntimeError("Resource exhausted"),
        ]
    )
    async_client = MagicMock()
    async_client.models = async_models
    mock_client = MagicMock()
    mock_client.async_client = async_client

    async def fake_sleep(_duration: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    @do
    def flow() -> EffectGenerator[Any]:
        return (
            yield edit_image__gemini(
                prompt="Enhance image",
                model="gemini-2.5-flash-image-preview",
                max_retries=2,
            )
        )

    engine = ProgramInterpreter()
    ctx = ExecutionContext(env={"gemini_client": mock_client})
    result = await engine.run_async(flow(), ctx)

    assert result.is_err
    error = result.result.error
    while hasattr(error, "cause") and getattr(error, "cause") is not None:
        error = error.cause
    assert isinstance(error, RuntimeError)
    assert str(error) == "Resource exhausted"

    structured_logs = [entry for entry in result.log if isinstance(entry, dict)]
    failure_logs = [
        entry for entry in structured_logs if entry.get("event") == "gemini.retry_exhausted"
    ]
    assert failure_logs
    failure_entry = failure_logs[-1]
    assert failure_entry["level"] == "ERROR"
    assert failure_entry["attempts"] == 2
    assert failure_entry["model"] == "gemini-2.5-flash-image-preview"
    assert "Resource exhausted" in failure_entry["error"]
