from __future__ import annotations

from dataclasses import dataclass

from doeff import EffectBase, Pass, Pure, Resume, do, handler, run, with_handlers


@dataclass(frozen=True)
class Ping(EffectBase):
    value: str


@do
def outer(effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{effect.value}:outer"))
    yield Pass(effect, k)


@do
def inner(effect, k):
    if isinstance(effect, Ping):
        return (yield Resume(k, f"{effect.value}:inner"))
    yield Pass(effect, k)


@do
def body():
    return (yield Ping("start"))


def test_with_handlers_applies_stack_left_to_right() -> None:
    program = with_handlers([handler(outer), handler(inner)], body())

    assert run(program) == "start:inner"


def test_with_handlers_accepts_empty_runtime_stack_as_identity() -> None:
    program = Pure("ok")

    assert with_handlers([], program) is program


def test_with_handlers_normalizes_raw_dispatchers() -> None:
    program = with_handlers([outer], body())

    assert run(program) == "start:outer"
