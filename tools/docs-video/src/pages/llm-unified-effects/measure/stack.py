# The stack scene: the page's workflow() (docs/llm_unified_effects.md, "Example: Multi-Provider Stack") under
# the same three-provider stack, with each provider package's mock handler in place of its production handler,
# so no API is called. Each mock is wrapped to print every effect that reaches it; a mock that does not support
# the effect's model yields Pass, so the next handler out sees the same effect. Prints, in order, which
# handler saw each effect, which one answered (from the mocks' own call records), and workflow()'s value.
from doeff_gemini import MockGeminiHandler, gemini_mock_handler
from doeff_llm import LLMChat, LLMStructuredQuery
from doeff_openai import MockOpenAIConfig, MockOpenAIState, openai_mock_handler
from doeff_openrouter import MockOpenRouterRuntime, openrouter_mock_handler
from pydantic import BaseModel

from doeff import do, handler, run


class Analysis(BaseModel):
    verdict: str
    score: int


@do
def workflow():
    analysis = yield LLMStructuredQuery(
        messages=[{"role": "user", "content": "Analyze this code"}],
        response_format=Analysis,
        model="gpt-4o",
    )
    summary = yield LLMChat(
        messages=[{"role": "user", "content": f"Summarize: {analysis.verdict}"}],
        model="gemini-1.5-pro",
    )
    fallback = yield LLMChat(
        messages=[{"role": "user", "content": "Say hello"}],
        model="mistral-large-latest",
    )
    return analysis, summary, fallback


# The answers the mocks give back, in place of the providers' replies.
openai_state = MockOpenAIState()
openai_config = MockOpenAIConfig(structured_responses=[{"verdict": "clean", "score": 8}])
gemini = MockGeminiHandler(chat_responses={"gemini-1.5-pro": "The code is clean."})
router = MockOpenRouterRuntime(
    chat_response={"choices": [{"message": {"role": "assistant", "content": "Hello!"}}]},
)


# A provider's mock handler that first prints the effect reaching it, so the log shows each Pass.
def traced(name, mock, **options):
    @do
    def dispatch(effect, k):
        print(f"{name} sees {effect!r}")
        return (yield mock(effect, k, **options))

    return handler(dispatch)


openai = traced("openai", openai_mock_handler, config=openai_config, state=openai_state)
gemini_h = traced("gemini", gemini_mock_handler, handler=gemini)
openrouter = traced("openrouter", openrouter_mock_handler, runtime=router)

analysis, summary, fallback = run(openrouter(gemini_h(openai(workflow()))))

print("answered by openai:", [call["model"] for call in openai_state.calls])
print("answered by openrouter:", [call["model"] for call in router.calls])
print("analysis:", repr(analysis))
print("summary:", repr(summary))
print("fallback:", repr(fallback["choices"][0]["message"]["content"]))
