"""Demonstrate unified LLM effects with stacked provider handlers."""

from __future__ import annotations

from typing import Any

from doeff_gemini.handlers import gemini_mock_handler
from doeff_llm.effects import LLMChat, LLMStructuredOutput
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


@do
def workflow() -> EffectGenerator[dict[str, Any]]:
    analysis = yield LLMStructuredOutput(
        messages=[{"role": "user", "content": "Analyze this change"}],
        response_format=AnalysisResult,
        model="gpt-4o",
    )
    summary = yield LLMChat(
        messages=[{"role": "user", "content": f"Summarize verdict={analysis.verdict}"}],
        model="gemini-1.5-pro",
    )
    fallback = yield LLMChat(
        messages=[{"role": "user", "content": "Reply from fallback provider"}],
        model="mistral-large-latest",
    )
    return {
        "analysis": analysis,
        "summary": summary,
        "fallback": fallback,
    }


def main() -> None:
    openai_state = MockOpenAIState()
    openai_config = MockOpenAIConfig(
        structured_responses=[{"verdict": "clean", "score": 8}],
    )
    router_runtime = MockOpenRouterRuntime(
        chat_response={
            "id": "router-fallback",
            "choices": [{"message": {"role": "assistant", "content": "fallback response"}}],
        },
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

    def router_handler(effect: Any, k: Any):
        return (yield from openrouter_mock_handler(effect, k, runtime=router_runtime))

    result = run(
        WithHandler(
            router_handler,  # catch-all fallback
            WithHandler(
                gemini_handler,
                WithHandler(openai_handler, workflow()),
            ),
        ),
        handlers=default_handlers(),
    )

    if result.is_err():
        raise result.error

    print("analysis:", result.value["analysis"])
    print("summary:", result.value["summary"])
    print("fallback:", result.value["fallback"])


if __name__ == "__main__":
    main()
