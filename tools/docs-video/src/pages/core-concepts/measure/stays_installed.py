# The "stays installed" scene: after(job()) from docs/02-core-concepts.md. after is installed once and receives
# both Steps; the second comes from inside k. Prints each event in the order it happens: a Step reaching after,
# k being resumed, and the result Resume gives back to after.
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Step(EffectBase):
    name: str


@do
def job():
    yield Step("load")
    yield Step("train")
    print('job returns "model"')
    return "model"


@handler
@do(non_tail=True)
def after(effect: Step, k):
    print(f"after receives Step({effect.name!r}) and resumes k")
    result = yield Resume(k, None)
    print(f"Resume gives after {effect.name!r} back {result!r}")
    return f"{result} after {effect.name}"


answer = run(after(job()))
print("answer:", answer)
