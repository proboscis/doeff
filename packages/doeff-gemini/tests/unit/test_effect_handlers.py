# ruff: noqa: E402, I001
"""Tests for Gemini effect and handler modules."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

try:
    from pydantic import BaseModel
except ModuleNotFoundError:
    import types

    pydantic_stub = types.ModuleType("pydantic")

    class ValidationError(Exception):
        """Stub ValidationError used by tests when pydantic is unavailable."""

    class BaseModel:  # type: ignore[no-redef]
        """Minimal pydantic-like stub supporting model_validate."""

        model_fields: ClassVar[dict[str, Any]] = {}

        def __init_subclass__(cls, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            annotations = getattr(cls, "__annotations__", {})
            cls.model_fields = {
                name: SimpleNamespace(annotation=annotation)
                for name, annotation in annotations.items()
            }

        def __init__(self, **data: Any) -> None:
            for field_name in self.model_fields:
                setattr(self, field_name, data[field_name])

        @classmethod
        def model_validate(cls, value: Any) -> Any:
            if isinstance(value, cls):
                return value
            if not isinstance(value, dict):
                raise ValidationError("Expected mapping payload")
            return cls(**value)

    pydantic_stub.BaseModel = BaseModel  # type: ignore[attr-defined]
    pydantic_stub.ValidationError = ValidationError  # type: ignore[attr-defined]
    sys.modules["pydantic"] = pydantic_stub
    from pydantic import BaseModel

from doeff import EffectGenerator, do, run_with_handler_map
from doeff_gemini.effects import GeminiChat, GeminiEmbedding, GeminiStructuredOutput
from doeff_gemini.handlers import mock_handlers, production_handlers


class FunFact(BaseModel):
    topic: str
    fact: str


@do
def _domain_program() -> EffectGenerator[dict[str, Any]]:
    chat = yield GeminiChat(
        messages=[{"role": "user", "content": "Say hello"}],
        model="gemini-test-model",
        temperature=0.1,
    )
    structured = yield GeminiStructuredOutput(
        messages=[{"role": "user", "content": "Return a hummingbird fact"}],
        response_format=FunFact,
        model="gemini-test-model",
    )
    embedding = yield GeminiEmbedding(
        input=["alpha", "beta"],
        model="text-embedding-004",
    )
    return {
        "chat": chat,
        "structured": structured,
        "embedding": embedding,
    }


def test_effect_exports() -> None:
    from doeff_gemini.effects import GeminiChat as ImportedChat
    from doeff_gemini.effects import GeminiStructuredOutput as ImportedStructured

    assert ImportedChat is GeminiChat
    assert ImportedStructured is GeminiStructuredOutput


def test_handler_exports() -> None:
    from doeff_gemini.handlers import mock_handlers as imported_mock_handlers
    from doeff_gemini.handlers import production_handlers as imported_production_handlers

    assert imported_production_handlers is production_handlers
    assert imported_mock_handlers is mock_handlers


def test_mock_handlers_are_configurable_and_deterministic() -> None:
    handlers = mock_handlers(
        chat_responses={"gemini-test-model": "mocked-chat-response"},
        structured_responses={
            FunFact: {
                "topic": "Hummingbirds",
                "fact": "They can fly backward.",
            }
        },
        embedding_dimensions=6,
        embedding_seed=17,
    )

    first = run_with_handler_map(_domain_program(), handlers)
    second = run_with_handler_map(_domain_program(), handlers)

    assert first.is_ok()
    assert second.is_ok()

    first_payload = first.value
    second_payload = second.value

    assert first_payload["chat"] == "mocked-chat-response"
    assert isinstance(first_payload["structured"], FunFact)
    assert first_payload["structured"].topic == "Hummingbirds"
    assert len(first_payload["embedding"]) == 2
    assert len(first_payload["embedding"][0]) == 6
    assert first_payload["embedding"] == second_payload["embedding"]


def test_handler_swapping_changes_behavior() -> None:
    @do
    def chat_program() -> EffectGenerator[str]:
        return (
            yield GeminiChat(
                messages=[{"role": "user", "content": "hello"}],
                model="gemini-swap-model",
            )
        )

    mock_result = run_with_handler_map(
        chat_program(),
        mock_handlers(chat_responses={"gemini-swap-model": "mock-result"}),
    )

    @do
    def production_chat(effect: GeminiChat) -> EffectGenerator[str]:
        return f"production:{effect.model}:{len(effect.messages)}"

    production_result = run_with_handler_map(
        chat_program(),
        production_handlers(chat_impl=production_chat),
    )

    assert mock_result.is_ok()
    assert production_result.is_ok()
    assert mock_result.value == "mock-result"
    assert production_result.value == "production:gemini-swap-model:1"
