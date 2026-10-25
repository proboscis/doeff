# The dispatch scene: greeting() yields Ask and Fetch under typed(...) inside reader(...). Each effect travels
# outward to the innermost handler whose annotation matches it; Ask skips typed without calling it.
# Prints which handler each effect reached, in order, and the answer (docs/02-core-concepts.md, "Pass the
# effects a handler does not answer").
from dataclasses import dataclass

from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader

from doeff import EffectBase, Resume, WithObserve, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@handler
def typed(effect: Fetch, k):
    print("typed is called for:", effect)
    return (yield Resume(k, f"got {effect.url}"))


@do
def greeting():
    name = yield Ask("name")
    body = yield Fetch("https://example.com")
    return f"{name}: {body}"


def seen(effect):
    print("yielded:", effect)


answer = run(reader(env={"name": "ada"})(typed(WithObserve(seen, greeting()))))
print("answer:", answer)
