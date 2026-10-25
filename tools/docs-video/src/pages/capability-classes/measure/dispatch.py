# Class 0, dispatch: the handler performs the operation and resumes k once, immediately (docs/22-capability-classes.md,
# "Class 0 — Dispatch"). One program, summarize(), runs under a handler that routes by model prefix and under a test
# handler; the code after the yield runs once under each. The same answer comes from a plain function with the client
# passed in: class 0 is the runtime power of dependency injection.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Complete(EffectBase):
    model: str
    prompt: str


PROVIDERS = {"gpt": "openai", "claude": "anthropic"}


@do
def summarize(text):
    summary = yield Complete(
        "gpt-4o", f"summarize: {text}")
    print("  summarize(): the code after the yield runs")
    return summary


@handler
def routed(effect: Complete, k):
    provider = PROVIDERS[effect.model.split("-")[0]]
    return (yield Resume(k, f"{provider}: summary"))


@handler
def canned(effect: Complete, k):
    return (yield Resume(k, "canned summary"))


print('routed(summarize("the report")):')
print(f"  -> {run(routed(summarize('the report')))!r}")
print('canned(summarize("the report")):')
print(f"  -> {run(canned(summarize('the report')))!r}")


# The same choice with dependency injection: the client is passed in, and called once.
def summarize_di(text, complete):
    return complete("gpt-4o", f"summarize: {text}")


def canned_client(model, prompt):
    return "canned summary"


print('summarize_di("the report", canned_client):')
print(f"  -> {summarize_di('the report', canned_client)!r}")
