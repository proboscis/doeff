"""handler() also accepts a partial of an @do function and a bound method binding an @do method.
with_handlers / Spawn accept the handlers made from them.

Measured (agora-controllers' budget controller, 2026-09-23): the runtime's handler returned a
functools.partial, and handler() read ``raw_handler.__name__`` and
failed with AttributeError (both in with_handlers and in the scheduler that rewraps handlers at the Spawn boundary).
The name is taken from the inner function for a partial, and from the method for a bound method.

A handler body is only a def or an @do function (ADR-DOE-CORE-003). A callable instance is neither, so it is refused.
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
    """To read the name of the installer made by handler() (the installer is a function -- the type ProgramHandler does not declare a function name)."""
    assert isinstance(install, types.FunctionType), install
    return install.__name__


def test_installer_is_labelled_by_the_wrapped_function() -> None:
    assert _installer_name(handler(functools.partial(prefixed, "p:"))) == "prefixed"
    assert (
        _installer_name(handler(functools.partial(functools.partial(prefixed), "p:"))) == "prefixed"
    )
    assert _installer_name(handler(PrefixHandler("m:").method)) == "method"
