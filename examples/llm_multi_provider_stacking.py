"""Demonstrate unified LLM effects with stacked provider handlers."""

from __future__ import annotations

from functools import partial
from typing import Any

from doeff_gemini import gemini_mock_handler
from doeff_llm.effects import LLMChat, LLMStructuredQuery
from doeff_openai.handlers import (
    MockOpenAIConfig,
    MockOpenAIState,
    openai_mock_handler,
)
from doeff_openrouter.handlers import MockOpenRouterRuntime, openrouter_mock_handler
from pydantic import BaseModel

from doeff import EffectGenerator, do, handler, run


class AnalysisResult(BaseModel):
    verdict: str
    score: int


@do
def workflow() -> EffectGenerator[dict[str, Any]]:
    analysis = yield LLMStructuredQuery(
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

    openai_handler = handler(partial(openai_mock_handler, config=openai_config, state=openai_state))
    gemini_handler = handler(
        partial(gemini_mock_handler, chat_responses={"gemini-1.5-pro": "Gemini summary"})
    )
    router_handler = handler(partial(openrouter_mock_handler, runtime=router_runtime))

    # The innermost handler sees each effect first; openrouter is the catch-all fallback.
    result = run(router_handler(gemini_handler(openai_handler(workflow()))))

    print("analysis:", result["analysis"])
    print("summary:", result["summary"])
    print("fallback:", result["fallback"])


if __name__ == "__main__":
    main()
