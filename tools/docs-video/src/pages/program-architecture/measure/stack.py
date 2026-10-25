# The handler stack scene: h0(h1(h2(program()))). Each handler answers one kind of effect and passes the others
# outward. Prints, in order, which handler each effect reaches and which one answers it, and the answers the
# program returns.
from dataclasses import dataclass

from doeff import EffectBase, Pass, Resume, WithObserve, do, handler, run


@dataclass(frozen=True)
class Greet(EffectBase):
    name: str


@dataclass(frozen=True)
class Lookup(EffectBase):
    key: str


@dataclass(frozen=True)
class Save(EffectBase):
    value: str


def answering(name, kind, answer):
    @handler
    def h(effect, k):
        if isinstance(effect, kind):
            print(f"  {name} answers {type(effect).__name__}")
            return (yield Resume(k, answer(effect)))
        print(f"  {name} passes {type(effect).__name__}")
        yield Pass(effect, k)

    return h


h2 = answering("h2", Greet, lambda e: f"hi {e.name}")
h1 = answering("h1", Lookup, lambda e: "ada")
h0 = answering("h0", Save, lambda e: "saved")


@do
def program():
    a = yield Greet("bob")
    b = yield Lookup("user")
    c = yield Save("x")
    return (a, b, c)


def yields(effect):  # prints each effect as the program yields it
    print(f"program yields {type(effect).__name__}")


print("result:", run(h0(h1(h2(WithObserve(yields, program()))))))
