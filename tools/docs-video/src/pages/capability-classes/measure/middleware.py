# Class 1, data middleware: k is still resumed once, immediately, but the handler does something around that one
# resume (docs/22-capability-classes.md, "Class 1 — Data Middleware"). Two handlers over the same Complete effect:
#   memo  inspects the effect as data (a frozen dataclass is its own cache key) and elides the call on a hit;
#   retry re-yields the effect (a fresh attempt each time) and resumes k once, with the first success.
# Both re-dispatch with `yield effect`: the effect goes on to the rest of the stack, and its answer comes back as a value.
from dataclasses import dataclass

from doeff_core_effects import try_handler

from doeff import EffectBase, Resume, Try, do, handler, run


@dataclass(frozen=True)
class Complete(EffectBase):
    prompt: str


CALLS = []


@handler
def provider(effect: Complete, k):
    CALLS.append(effect.prompt)
    return (yield Resume(k, f"answer to {effect.prompt!r}"))


@do
def three_questions():
    a = yield Complete("plan")
    b = yield Complete("risks")
    c = yield Complete("plan")
    return [a, b, c]


def make_memo():
    cache = {}

    @handler
    def memo(effect: Complete, k):
        if effect not in cache:
            cache[effect] = yield effect  # re-dispatch: the next handler out performs it
        return (yield Resume(k, cache[effect]))

    return memo


CALLS.clear()
answers = run(provider(make_memo()(three_questions())))
print("memo: three Complete effects")
print(f"  answers: {answers!r}")
print(f"  provider calls: {CALLS!r}")

ATTEMPTS = []


@handler
def flaky(effect: Complete, k):
    ATTEMPTS.append(effect.prompt)
    if len(ATTEMPTS) <= 2:
        print(f"  provider: attempt {len(ATTEMPTS)} raises TimeoutError")
        raise TimeoutError("provider timed out")
    print(f"  provider: attempt {len(ATTEMPTS)} answers")
    return (yield Resume(k, f"answer to {effect.prompt!r}"))


@handler
def retry(effect: Complete, k):
    last = None
    for attempt in range(3):
        safe = yield Try(effect)  # re-yield the effect: a fresh continuation per attempt
        if safe.is_ok():
            print(f"  retry: attempt {attempt + 1} ok, resumes k once")
            return (yield Resume(k, safe.value))
        last = safe.error
    raise last


@do
def one_question():
    answer = yield Complete("plan")
    print("  one_question(): the code after the yield runs")
    return answer


print("retry: one Complete effect, a provider that fails twice")
print(f"  -> {run(try_handler(flaky(retry(one_question()))))!r}")
