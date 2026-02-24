"""Cross-provider handler composition tests for unified LLM effects."""

from __future__ import annotations

from typing import Any

from doeff_gemini.handlers import gemini_mock_handler
from doeff_llm.effects import LLMChat, LLMStructuredQuery
from doeff_openai.handlers import (
    MockOpenAIConfig,
    MockOpenAIState,
    openai_mock_handler,
)
from doeff_openrouter.handlers import MockOpenRouterRuntime, openrouter_mock_handler
from pydantic import BaseModel

from doeff import EffectGenerator, WithHandler, default_handlers, do, run


class AnalysisResult(BaseModel):
    verdict: str
    score: int


def test_multi_model_workflow_routes_to_openai_then_gemini() -> None:
    openai_state = MockOpenAIState()
    openai_config = MockOpenAIConfig(
        structured_responses=[{"verdict": "clean", "score": 9}],
    )

    def openai_handler(effect: Any, k: Any):
        return (
            yield from openai_mock_handler(
                effect,
                k,
                config=openai_config,
                state=openai_state,
            )
        )

    def gemini_handler(effect: Any, k: Any):
        return (
            yield from gemini_mock_handler(
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

    result = run(
        WithHandler(openrouter_mock_handler, workflow()),
        handlers=default_handlers(),
    )

    assert result.is_ok()
    assert result.value["id"] == "mock-router-chat"

    # Run once more with explicit runtime to verify call capture on catch-all routing.
    def router_handler(effect: Any, k: Any):
        return (yield from openrouter_mock_handler(effect, k, runtime=runtime))

    routed = run(
        WithHandler(router_handler, workflow()),
        handlers=default_handlers(),
    )

    assert routed.is_ok()
    assert routed.value["id"] == "fallback"
    assert runtime.calls
    assert runtime.calls[0]["model"] == "mistral-large-latest"
