"""Test do! macro with setv — regression test for variable scoping.

do! expands to @do(fn [] ...) which is a generator function.
setv inside do! must work correctly — variables assigned before yield
must be accessible after yield.
"""

import hy  # noqa: F401

from doeff import WithHandler, do, run
from doeff_core_effects import slog
from doeff_core_effects.handlers import lazy_ask, writer
from doeff_core_effects.scheduler import scheduled


def _run_program(program):
    composed = WithHandler(
        lazy_ask(env={}),
        WithHandler(writer(), program),
    )
    return run(scheduled(composed))


def test_do_bang_setv_basic():
    """setv inside do! should work — variable accessible in same scope."""
    # This is written in Python calling Hy-compiled code
    # to test the do! macro behavior
    @do
    def program():
        # Equivalent to: (do! (setv x 42) x)
        x = 42
        return x

    result = _run_program(program())
    assert result == 42


def test_do_bang_setv_after_yield():
    """setv before yield, access after yield — the critical case."""

    @do
    def program():
        x = 42
        yield slog(msg="test")
        y = x + 1  # x must still be accessible after yield
        return y

    result = _run_program(program())
    assert result == 43


def test_do_bang_setv_with_yield_result():
    """setv from yield result, then use later."""

    @do
    def program():
        x = 10
        yield slog(msg=f"x={x}")
        y = x * 2
        yield slog(msg=f"y={y}")
        return y

    result = _run_program(program())
    assert result == 20
