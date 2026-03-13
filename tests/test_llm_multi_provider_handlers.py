"""Cross-provider handler composition tests for unified LLM effects."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from doeff_gemini.handlers import gemini_mock_handler
from doeff_llm.effects import LLMChat, LLMStructuredQuery
from doeff_openai.handlers import (
    MockOpenAIConfig,
    MockOpenAIState,
    openai_mock_handler,
)
from doeff_openrouter.handlers import MockOpenRouterRuntime, openrouter_mock_handler

try:
    from pydantic import BaseModel as _PydanticBaseModel

    class _PydanticCompatibilityProbe(_PydanticBaseModel):
        value: str

    BaseModel = _PydanticBaseModel
except Exception:
    class BaseModel:  # type: ignore[no-redef]
        """Minimal pydantic-like stub for Python 3.14t test environments."""

        model_fields: dict[str, Any] = {}

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
                raise TypeError("Expected mapping payload")
            return cls(**value)

from doeff import Effect, EffectGenerator, WithHandler, default_handlers, do, run


class AnalysisResult(BaseModel):
    verdict: str
    score: int


def test_multi_model_workflow_routes_to_openai_then_gemini() -> None:
    openai_state = MockOpenAIState()
    openai_config = MockOpenAIConfig(
        structured_responses=[{"verdict": "clean", "score": 9}],
    )

    @do
    def openai_handler(effect: Effect, k: Any):
        return (
            yield openai_mock_handler(
                effect,
                k,
                config=openai_config,
                state=openai_state,
            )
        )

    @do
    def gemini_handler(effect: Effect, k: Any):
        return (
            yield gemini_mock_handler(
                effect,
                k,
                chat_responses={"gemini-1.5-pro": "Gemini summary"},
            )
        )

    @do
    def workflow() -> EffectGenerator[dict[str, Any]]:
        analysis = yield LLMStructuredQuery(
            messages=[{"role": "user", "content": "Analyze this code"}],
            response_format=AnalysisResult,
            model="gpt-4o",
        )
        summary = yield LLMChat(
            messages=[{"role": "user", "content": f"Summarize: {analysis.verdict}"}],
            model="gemini-1.5-pro",
        )
        return {
            "analysis": analysis,
            "summary": summary,
        }

    result = run(
        WithHandler(gemini_handler, WithHandler(openai_handler, workflow())),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert isinstance(result.value["analysis"], AnalysisResult)
    assert result.value["analysis"].verdict == "clean"
    assert result.value["analysis"].score == 9
    assert result.value["summary"] == "Gemini summary"

    # OpenAI handled only the GPT-4o structured call and delegated Gemini chat.
    assert openai_state.structured_calls == 1
    assert openai_state.chat_calls == 0


def test_openrouter_catch_all_can_handle_unmatched_model() -> None:
    runtime = MockOpenRouterRuntime(
        chat_response={"id": "fallback", "choices": [{"message": {"content": "fallback"}}]},
    )

    @do
    def workflow() -> EffectGenerator[Any]:
        return (
            yield LLMChat(
                messages=[{"role": "user", "content": "hello"}],
                model="mistral-large-latest",
            )
        )

    @do
    def catch_all_handler(effect: Effect, k: Any):
        return (yield openrouter_mock_handler(effect, k))

    result = run(
        WithHandler(catch_all_handler, workflow()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value["id"] == "mock-router-chat"

    # Run once more with explicit runtime to verify call capture on catch-all routing.
    @do
    def router_handler(effect: Effect, k: Any):
        return (yield openrouter_mock_handler(effect, k, runtime=runtime))

    routed = run(
        WithHandler(router_handler, workflow()),
        handlers=default_handlers(),
    )

    assert routed.is_ok()
    assert routed.value["id"] == "fallback"
    assert runtime.calls
    assert runtime.calls[0]["model"] == "mistral-large-latest"
