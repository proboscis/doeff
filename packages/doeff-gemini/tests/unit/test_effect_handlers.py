# ruff: noqa: E402, I001
"""Tests for Gemini effect and handler modules."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

IMAGE_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "doeff-image" / "src"
if str(IMAGE_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(IMAGE_PACKAGE_ROOT))

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

from doeff import (
    Delegate,
    EffectGenerator,
    Resume,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.effects.base import Effect
from doeff_image.effects import ImageEdit
from doeff_image.effects import ImageEdit as UnifiedImageEdit
from doeff_image.effects import ImageGenerate as UnifiedImageGenerate
from doeff_image.types import ImageResult
from doeff_llm.effects import LLMChat, LLMEmbedding, LLMStructuredQuery
from doeff_gemini.effects import (
    GeminiChat,
    GeminiEmbedding,
    GeminiImageEdit,
    GeminiStructuredOutput,
)
from doeff_gemini.handlers import (
    gemini_mock_handler,
    gemini_production_handler,
    mock_handlers,
    production_handlers,
)


class FunFact(BaseModel):
    topic: str
    fact: str


def _run_with_handler(program, handler):
    return run(
        WithHandler(handler, program),
        handlers=default_handlers(),
    )


@do
def _domain_program() -> EffectGenerator[dict[str, Any]]:
    chat = yield LLMChat(
        messages=[{"role": "user", "content": "Say hello"}],
        model="gemini-test-model",
        temperature=0.1,
    )
    structured = yield LLMStructuredQuery(
        messages=[{"role": "user", "content": "Return a hummingbird fact"}],
        response_format=FunFact,
        model="gemini-test-model",
    )
    embedding = yield LLMEmbedding(
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
    from doeff_gemini.effects import GeminiImageEdit as ImportedImageEdit
    from doeff_gemini.effects import GeminiStructuredOutput as ImportedStructured

    assert ImportedChat is GeminiChat
    assert ImportedImageEdit is GeminiImageEdit
    assert ImportedStructured is GeminiStructuredOutput
    assert issubclass(GeminiChat, LLMChat)
    assert issubclass(GeminiStructuredOutput, LLMStructuredQuery)
    assert issubclass(GeminiEmbedding, LLMEmbedding)


def test_deprecated_effect_aliases_emit_warnings() -> None:
    import pytest

    with pytest.deprecated_call(match="GeminiChat is deprecated"):
        GeminiChat(
            messages=[{"role": "user", "content": "hi"}],
            model="gemini-1.5-pro",
        )
    with pytest.deprecated_call(match="GeminiStructuredOutput is deprecated"):
        GeminiStructuredOutput(
            messages=[{"role": "user", "content": "json"}],
            response_format=FunFact,
            model="gemini-1.5-pro",
        )
    with pytest.deprecated_call(match="GeminiEmbedding is deprecated"):
        GeminiEmbedding(
            input="embed",
            model="text-embedding-004",
        )


def test_gemini_image_edit_is_deprecated_alias() -> None:
    with pytest.deprecated_call(match="GeminiImageEdit is deprecated"):
        effect = GeminiImageEdit(prompt="deprecated", model="gemini-3-pro-image")
    assert isinstance(effect, ImageEdit)


def test_handler_exports() -> None:
    from doeff_gemini.handlers import mock_handlers as imported_mock_handlers
    from doeff_gemini.handlers import production_handlers as imported_production_handlers

    assert imported_production_handlers is production_handlers
    assert imported_mock_handlers is mock_handlers
    assert callable(gemini_production_handler)
    assert callable(gemini_mock_handler)


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

    first = _run_with_handler(_domain_program(), handlers)
    second = _run_with_handler(_domain_program(), handlers)

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
            yield LLMChat(
                messages=[{"role": "user", "content": "hello"}],
                model="gemini-swap-model",
            )
        )

    mock_result = _run_with_handler(
        chat_program(),
        mock_handlers(chat_responses={"gemini-swap-model": "mock-result"}),
    )

    @do
    def production_chat(effect: LLMChat) -> EffectGenerator[str]:
        return f"production:{effect.model}:{len(effect.messages)}"

    production_result = _run_with_handler(
        chat_program(),
        production_handlers(chat_impl=production_chat),
    )

    assert mock_result.is_ok()
    assert production_result.is_ok()
    assert mock_result.value == "mock-result"
    assert production_result.value == "production:gemini-swap-model:1"


def test_gemini_handler_delegates_unsupported_models() -> None:
    @do
    def fallback_handler(effect: Effect, k: Any):
        if isinstance(effect, LLMChat):
            return (yield Resume(k, "fallback-chat"))
        yield Delegate()

    @do
    def program() -> EffectGenerator[str]:
        return (
            yield LLMChat(
                messages=[{"role": "user", "content": "route elsewhere"}],
                model="gpt-4o",
            )
        )

    result = run(
        WithHandler(fallback_handler, WithHandler(gemini_mock_handler, program())),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value == "fallback-chat"


@do
def _unified_image_program() -> EffectGenerator[ImageResult]:
    generated = yield UnifiedImageGenerate(
        prompt="A bright kite",
        model="gemini-3-pro-image",
    )
    edited = yield UnifiedImageEdit(
        prompt="Add clouds",
        model="gemini-3-pro-image",
        images=[generated.images[0]],
    )
    return edited


def test_mock_handlers_support_unified_image_effects() -> None:
    result = _run_with_handler(_unified_image_program(), mock_handlers())
    assert result.is_ok()
    assert isinstance(result.value, ImageResult)
