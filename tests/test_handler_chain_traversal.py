"""Minimum repro: handler-emitted effects don't traverse full handler chain.

When a handler in a deep WithHandler chain (29 handlers) handles an effect
and emits a new effect, that new effect should traverse the remaining outer
handlers. But with many handlers, it appears to skip some.

Works with 3 handlers, fails with 29.
"""

from doeff import do, WithHandler, run
from doeff_vm import EffectBase, Resume, Pass
from doeff_core_effects.scheduler import scheduled


class EffectA(EffectBase):
    """Handled by handler at position 9."""
    pass


class EffectB(EffectBase):
    """Emitted by handler_a, should be caught by handler at position 18."""
    pass


@do
def handler_a(effect, k):
    """Handles EffectA, emits EffectB."""
    if isinstance(effect, EffectA):
        result = yield EffectB()
        return (yield Resume(k, f"A({result})"))
    yield Pass(effect, k)


@do
def handler_b(effect, k):
    """Handles EffectB."""
    if isinstance(effect, EffectB):
        return (yield Resume(k, "B"))
    yield Pass(effect, k)


@do
def noop_handler(effect, k):
    """Pass-through handler — does nothing."""
    yield Pass(effect, k)


@do
def prog():
    return (yield EffectA())


def test_3_handlers():
    """Works: handler_a inner, noop middle, handler_b outer."""
    wrapped = prog()
    wrapped = WithHandler(handler_a, wrapped)
    wrapped = WithHandler(noop_handler, wrapped)
    wrapped = WithHandler(handler_b, wrapped)
    assert run(wrapped) == "A(B)"


def test_3_handlers_with_scheduled():
    """Works with scheduled."""
    wrapped = prog()
    wrapped = WithHandler(handler_a, wrapped)
    wrapped = WithHandler(noop_handler, wrapped)
    wrapped = WithHandler(handler_b, wrapped)
    assert run(scheduled(wrapped)) == "A(B)"


def test_29_handlers():
    """Repro: 29 handlers, handler_a at position 9, handler_b at position 18.

    EffectA is emitted by prog, caught by handler_a (pos 9).
    handler_a emits EffectB, which should traverse pos 10-18 and be caught by handler_b (pos 18).
    Positions 0-8 and 10-17 and 19-28 are noop handlers.
    """
    wrapped = prog()
    for i in range(29):
        if i == 9:
            wrapped = WithHandler(handler_a, wrapped)
        elif i == 18:
            wrapped = WithHandler(handler_b, wrapped)
        else:
            wrapped = WithHandler(noop_handler, wrapped)
    assert run(wrapped) == "A(B)"


def test_29_handlers_with_scheduled():
    """Same as above but with scheduled."""
    wrapped = prog()
    for i in range(29):
        if i == 9:
            wrapped = WithHandler(handler_a, wrapped)
        elif i == 18:
            wrapped = WithHandler(handler_b, wrapped)
        else:
            wrapped = WithHandler(noop_handler, wrapped)
    assert run(scheduled(wrapped)) == "A(B)"
