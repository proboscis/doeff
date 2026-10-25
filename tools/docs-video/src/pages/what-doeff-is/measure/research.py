# The mechanism scenes: research(topics) yields one Spend per topic, and handlers give Spend its meaning
# (docs/positioning/what-doeff-is.md, "The mechanism"): free resumes k, refuse drops it, budget(limit) keeps
# what is left between effects and resumes or drops k, and trace stacks inside budget and passes each Spend on.
# Prints what each handler did with each Spend, in order, and each run's result.
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, Transfer, do, handler, run


@dataclass(frozen=True)
class Spend(EffectBase):
    cost: int


@do
def research(topics):
    notes = []
    for topic in topics:
        yield Spend(3)
        print(f"  research goes on: notes on {topic}")
        notes.append(f"notes on {topic}")
    return notes


@do
def free(effect: Spend, k):
    print(f"  free: {effect} -> Transfer(k, None)")
    yield Transfer(k, None)


@do
def refuse(effect: Spend, k):
    print(f"  refuse: {effect} -> drops k")
    return "refused"


def budget(limit):
    left = limit

    @do
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.cost > left:
            print(f"  budget: {effect}, left {left} -> drops k")
            return "over budget"
        print(f"  budget: {effect}, left {left} -> {left - effect.cost}, resumes k")
        left -= effect.cost
        return (yield Resume(k, None))

    return handler(on_spend)


seen: list[object] = []


@do
def trace(effect, k):
    print(f"  trace: {effect} -> Pass")
    seen.append(effect)
    return (yield Pass(effect, k))


TOPICS = ["tides", "moons"]
print("free:")
print("  ->", run(handler(free)(research(TOPICS))))
print("refuse:")
print("  ->", run(handler(refuse)(research(TOPICS))))
print("budget(10):")
print("  ->", run(budget(10)(research(TOPICS))))
print("budget(5):")
print("  ->", run(budget(5)(research(TOPICS))))
print("budget(5) around trace:")
print("  ->", run(budget(5)(handler(trace)(research(TOPICS)))))
print("  seen:", seen)
