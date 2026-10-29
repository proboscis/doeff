# The shared-effects scene (docs/llm_unified_effects.md, "Why" and "Deprecated Provider-Specific Effect
# Names"): one program yields the same LLMChat whatever the provider; only the model name differs, and the
# handler stack decides who answers. The old provider-specific names are subclasses of the shared effects
# and warn when built. Prints the provider that answered each model, and what the old names are.
import warnings

from doeff_gemini.effects import GeminiChat
from doeff_gemini.handlers.testing import MockGeminiHandler, gemini_mock_handler
from doeff_llm import LLMChat
from doeff_openai import ChatCompletion, MockOpenAIConfig, openai_mock_handler
from doeff_openrouter import RouterChat

from doeff import do, handler, run


# The one program: asks a model, by name, to say hello.
@do
def hello(model):
    return (yield LLMChat(messages=[{"role": "user", "content": "Say hello"}], model=model))


# A provider package's mock handler with its recorded answer, installed as a handler of the stack.
def mock(dispatch, **options):
    @do
    def answer(effect, k):
        return (yield dispatch(effect, k, **options))

    return handler(answer)


openai = mock(openai_mock_handler, config=MockOpenAIConfig(chat_responses=["hi from openai"]))
gemini = mock(gemini_mock_handler, handler=MockGeminiHandler(chat_responses={"gemini-1.5-pro": "hi from gemini"}))

for model in ["gpt-4o", "gemini-1.5-pro"]:
    answer = run(gemini(openai(hello(model))))
    text = answer if isinstance(answer, str) else answer.choices[0].message.content
    print(f"hello({model!r}) -> {text!r}")

for old in [ChatCompletion, GeminiChat, RouterChat]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old(messages=[], model="any")
    print(
        f"{old.__module__}.{old.__name__}: subclass of LLMChat = {issubclass(old, LLMChat)},",
        f"warns {caught[0].category.__name__}",
    )
