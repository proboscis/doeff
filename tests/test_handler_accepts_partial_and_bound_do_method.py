"""handler() は @do 関数の partial と、@do の method を束縛した bound method も受ける。
with_handlers / Spawn は、それで作った handler を受ける。

実測(agora-controllers の予算の controller・2026-09-23): runtime の handler が
functools.partial を返し、handler() が ``raw_handler.__name__`` を読んで
AttributeError で落ちた(with_handlers と、Spawn の境目で handler を包み直す
scheduler の両方)。名前は partial なら中の関数・bound method なら method から取る。

handler の body は def か @do 関数だけ(ADR-DOE-CORE-003)。呼べる instance はどちらでもないので断る。
"""

import functools
import types
from dataclasses import dataclass

import pytest
from doeff_core_effects import scheduled

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

    @do
    def method(self, effect, k):
        if isinstance(effect, Ping):
            return (yield Resume(k, f"{self.prefix}{effect.n}"))
        yield Pass(effect, k)


@do
def body():
    return (yield Ping(1))


@do
def spawner():
    task = yield Spawn(body())
    return (yield Wait(task))


def test_with_handlers_accepts_a_handler_made_from_a_partial() -> None:
    assert run(with_handlers([handler(functools.partial(prefixed, "p:"))], body())) == "p:1"


def test_spawn_reinstalls_a_partial_handler_in_the_child_task() -> None:
    program = scheduled(with_handlers([handler(functools.partial(prefixed, "p:"))], spawner()))
    assert run(program) == "p:1"


def test_handler_accepts_a_bound_do_method_and_refuses_a_callable_instance() -> None:
    assert run(handler(PrefixHandler("m:").method)(body())) == "m:1"
    with pytest.raises(TypeError, match=r"PrefixHandler is not a handler body"):
        handler(PrefixHandler("c:"))


def _installer_name(install: object) -> str:
    """handler() が作った installer の名を読むため(installer は関数 — 型の ProgramHandler は関数の名を宣言しない)。"""
    assert isinstance(install, types.FunctionType), install
    return install.__name__


def test_installer_is_labelled_by_the_wrapped_function() -> None:
    assert _installer_name(handler(functools.partial(prefixed, "p:"))) == "prefixed"
    assert (
        _installer_name(handler(functools.partial(functools.partial(prefixed), "p:"))) == "prefixed"
    )
    assert _installer_name(handler(PrefixHandler("m:").method)) == "method"
