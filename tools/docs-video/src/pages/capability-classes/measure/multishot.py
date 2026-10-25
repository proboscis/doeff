# Class 3, multi-shot, is deliberately absent (docs/22-capability-classes.md, "Class 3 — Multi-shot"). A handler
# that resumes k a second time raises RuntimeError: continuations are one-shot. The sanctioned approximation is to
# run the whole Program again with a memoized prefix: two runs of what_if() explore two branches, and a memo shared
# between the runs makes the expensive call before the branch point happen once.
from dataclasses import dataclass

from doeff_core_effects.handlers import reader

from doeff import Ask, EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Complete(EffectBase):
    prompt: str


@do
def what_if():
    plan = yield Complete("plan")
    branch = yield Ask("branch")
    return f"{plan} -> {branch}"


@handler
@do(non_tail=True)
def twice(effect: Complete, k):
    yield Resume(k, "first")
    return (yield Resume(k, "second"))


print("a handler that resumes k twice:")
try:
    run(reader(env={"branch": "a"})(twice(what_if())))
except RuntimeError as error:
    print(f"  raises RuntimeError: {error}")

CALLS = []


@handler
def provider(effect: Complete, k):
    CALLS.append(effect.prompt)
    return (yield Resume(k, "plan v1"))


CACHE = {}


@handler
def memo(effect: Complete, k):
    if effect not in CACHE:
        CACHE[effect] = yield effect
    return (yield Resume(k, CACHE[effect]))


print("re-execute what_if() per branch, with one memo shared by the runs:")
for branch in ["a", "b"]:
    answer = run(provider(memo(reader(env={"branch": branch})(what_if()))))
    print(f"  branch {branch}: {answer!r}")
print(f"  provider calls: {CALLS!r}")
