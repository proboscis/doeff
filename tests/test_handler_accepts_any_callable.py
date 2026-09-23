"""handler() / with_handlers / Spawn は callable なら何でも handler として受ける。

実測(agora-controllers の予算の controller・2026-09-23): runtime の handler が
functools.partial を返し、handler() が ``raw_handler.__name__`` を読んで
AttributeError で落ちた(with_handlers と、Spawn の境目で handler を包み直す
scheduler の両方)。VM が要るのは callable であることだけなので、名前は
partial なら中の関数・呼べる instance なら class から取る。
"""

import functools
from dataclasses import dataclass

from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Pass, Resume, Spawn, Wait, do, handler, run, with_handlers


@dataclass(frozen=True)
class Ping(EffectBase):
    n: int


@do
def prefixed(prefix, effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{prefix}{effect.n}"))
    yield Pass(effect, k)


class PrefixHandler:
    def __init__(self, prefix):
        self.prefix = prefix

    def __call__(self, effect, k):
        return prefixed(self.prefix, effect, k)

    def method(self, effect, k):
        return prefixed(self.prefix, effect, k)


@do
def body():
    return (yield Ping(1))


@do
def spawner():
    task = yield Spawn(body())
    return (yield Wait(task))


def test_with_handlers_accepts_partial() -> None:
    assert run(with_handlers([functools.partial(prefixed, "p:")], body())) == "p:1"


def test_spawn_reinstalls_partial_handler_in_the_child_task() -> None:
    program = scheduled(with_handlers([functools.partial(prefixed, "p:")], spawner()))
    assert run(program) == "p:1"


def test_handler_accepts_callable_instance_and_bound_method() -> None:
    assert run(handler(PrefixHandler("c:"))(body())) == "c:1"
    assert run(handler(PrefixHandler("m:").method)(body())) == "m:1"


def test_installer_is_labelled_by_the_wrapped_function() -> None:
    assert handler(functools.partial(prefixed, "p:")).__name__ == "prefixed"
    assert handler(functools.partial(functools.partial(prefixed), "p:")).__name__ == "prefixed"
    assert handler(PrefixHandler("c:")).__name__ == "PrefixHandler"
    assert handler(PrefixHandler("m:").method).__name__ == "method"
