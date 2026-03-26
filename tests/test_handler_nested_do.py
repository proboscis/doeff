"""Repro: effects from nested @do functions inside a handler.

When a handler calls a nested @do helper that emits effects,
do those effects reach the outer handler stack?
"""

from doeff import do, WithHandler, run
from doeff_vm import EffectBase, Resume, Pass
from doeff_core_effects.scheduler import scheduled


class EffectA(EffectBase):
    pass

class EffectB(EffectBase):
    pass


@do
def _helper():
    """Nested @do function that emits EffectB."""
    return (yield EffectB())


@do
def handler_a(effect, k):
    """Handles EffectA, delegates to _helper which emits EffectB."""
    if isinstance(effect, EffectA):
        # Call nested @do function that emits EffectB
        result = yield _helper()
        return (yield Resume(k, f"A({result})"))
    yield Pass(effect, k)


@do
def handler_b(effect, k):
    if isinstance(effect, EffectB):
        return (yield Resume(k, "B"))
    yield Pass(effect, k)


@do
def prog():
    return (yield EffectA())


def test_nested_do_in_handler():
    """Handler calls nested @do that emits effect."""
    wrapped = prog()
    wrapped = WithHandler(handler_a, wrapped)
    wrapped = WithHandler(handler_b, wrapped)
    assert run(wrapped) == "A(B)"


def test_nested_do_in_handler_with_scheduled():
    """Same with scheduled."""
    wrapped = prog()
    wrapped = WithHandler(handler_a, wrapped)
    wrapped = WithHandler(handler_b, wrapped)
    assert run(scheduled(wrapped)) == "A(B)"


@do
def _deep_helper():
    """Two levels of nesting."""
    return (yield _helper())


@do
def handler_a_deep(effect, k):
    """Handles EffectA via two levels of @do nesting."""
    if isinstance(effect, EffectA):
        result = yield _deep_helper()
        return (yield Resume(k, f"A({result})"))
    yield Pass(effect, k)


def test_deep_nested_do_in_handler():
    """Two levels of @do nesting inside handler."""
    wrapped = prog()
    wrapped = WithHandler(handler_a_deep, wrapped)
    wrapped = WithHandler(handler_b, wrapped)
    assert run(scheduled(wrapped)) == "A(B)"
