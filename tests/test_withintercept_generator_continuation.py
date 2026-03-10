"""Reproduce: generator does not continue after yield WithIntercept(...).

When a @do generator yields WithIntercept(...), the VM returns the
sub-program's result but does NOT continue the generator. Any code
after the WithIntercept yield is silently skipped.

    @do
    def my_program():
        result = yield WithIntercept(interceptor, sub_program())
        # ← everything below here is never reached
        side_effect = yield SomeEffect()
        return (result, side_effect)

This affects any program that needs to perform work after observing
a sub-program's effects via WithIntercept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import doeff_vm

from doeff import (
    Effect,
    EffectBase,
    WithHandler,
    WithIntercept,
    default_handlers,
    do,
    run,
)


@dataclass(frozen=True)
class FooFx(EffectBase):
    label: str
    val: int


@do
def foo_handler(effect: FooFx, k: Any):
    if not isinstance(effect, FooFx):
        yield doeff_vm.Pass()
        return
    return (yield doeff_vm.Resume(k, effect.val * 10))


@do
def _sub_program():
    return (yield FooFx(label="sub", val=5))


@do
def _interceptor(effect: Effect):
    return effect


def _run(program):
    return run(
        WithHandler(foo_handler, program),
        handlers=[*default_handlers()],
    )


def test_generator_continues_after_sub_program():
    """Baseline: yield sub-program, then yield another effect."""

    @do
    def prog():
        first = yield _sub_program()
        second = yield FooFx(label="after", val=2)
        return (first, second)

    result = _run(prog())
    assert result.is_ok(), f"Expected ok, got: {result.error}"
    assert result.value == (50, 20)


def test_generator_continues_after_withintercept():
    """Bug: yield WithIntercept(...), then yield another effect.

    The generator should continue after the WithIntercept completes,
    but the VM returns the WithIntercept result immediately without
    resuming the generator.
    """

    @do
    def prog():
        first = yield WithIntercept(
            _interceptor,
            _sub_program(),
            types=(FooFx,),
            mode="include",
        )
        second = yield FooFx(label="after", val=2)
        return (first, second)

    result = _run(prog())
    assert result.is_ok(), f"Expected ok, got: {result.error}"
    # BUG: result.value is 50 (just the WithIntercept result),
    # not (50, 20) as expected.
    assert result.value == (50, 20), (
        f"Generator did not continue after WithIntercept. "
        f"Expected (50, 20), got {result.value}"
    )
