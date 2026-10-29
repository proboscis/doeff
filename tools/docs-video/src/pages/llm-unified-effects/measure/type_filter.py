# The type-filter scene (docs/llm_unified_effects.md, "Model-Based Routing"): a handler annotated
# `effect: LLMChat` is never called for an LLMStructuredQuery. The installed scope carries the annotation as a
# runtime filter, so the structured query skips chat_only and goes on to any_model, which takes every effect.
# Prints which handler was called for each effect, and the answers.
from doeff_llm import LLMChat, LLMStructuredQuery
from pydantic import BaseModel

from doeff import Resume, do, handler, run


class Analysis(BaseModel):
    verdict: str
    score: int


# Answers chat only: its annotation is the filter.
@handler
def chat_only(effect: LLMChat, k):
    print("chat_only is called for:", repr(effect))
    return (yield Resume(k, f"chat answer from {effect.model}"))


# Answers anything that reaches it, as a stand-in for a provider's catch-all handler.
@handler
def any_model(effect, k):
    print("any_model is called for:", repr(effect))
    return (yield Resume(k, Analysis(verdict="clean", score=8)))


@do
def both():
    analysis = yield LLMStructuredQuery(
        messages=[{"role": "user", "content": "Analyze this code"}],
        response_format=Analysis,
        model="gpt-4o",
    )
    reply = yield LLMChat(messages=[{"role": "user", "content": "Say hello"}], model="gpt-4o")
    return analysis, reply


analysis, reply = run(any_model(chat_only(both())))
print("analysis:", repr(analysis))
print("reply:", repr(reply))
