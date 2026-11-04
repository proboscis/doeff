# /// script
# requires-python = ">=3.11"
# dependencies = ["doeff==0.8.0"]
# ///
"""The order of events that docs/assets/figures/handler-k.html shows: budget(5)(research(TOPICS)) from the article, with a print at each event."""

from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Spend(EffectBase):
    cost: int


@do
def research(topics: list[str]):
    notes = []
    for topic in topics:
        print(f"research pauses at Spend for {topic}")
        yield Spend(3)
        notes.append(f"notes on {topic}")
        print(f"research goes on: notes = {notes}")
    return notes


def budget(limit: int):
    left = limit
    calls = 0

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left, calls
        calls += 1
        call = calls
        print(f"call {call} of on_spend receives {effect} and k, left = {left}")
        if effect.cost > left:
            print(f"call {call} returns 'over budget' without resuming k")
            return "over budget"
        left -= effect.cost
        print(f"call {call} takes it off the budget, left = {left}, and resumes k")
        answer = yield Resume(k, None)
        print(f"call {call} gets back {answer!r} from its Resume and returns it")
        return answer

    return on_spend


if __name__ == "__main__":
    print("run gives", repr(run(budget(5)(research(["tides", "moons"])))))
