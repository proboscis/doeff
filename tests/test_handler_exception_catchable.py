"""Bug: exceptions raised by non-@do handlers exit the VM immediately.

When a handler callable (not a @do generator) raises a Python exception,
call_handler in step.rs returns StepResult::Error — exiting the VM.
The error should instead propagate through Mode::Raise so that:

  1. Try effect can catch it as Err(exception)
  2. Native try/except around WithHandler can catch it

The @do handler case works because the exception occurs during generator
iteration (stream.resume → StreamStep::Error → Mode::Raise), not during
call_handler. Non-@do handlers raise during call_handler itself.

Additionally, the External call error path in pyvm.rs immediately exits
the VM instead of routing through receive_external_result(Err(...)).
"""

from dataclasses import dataclass

import pytest
from doeff_core_effects.handlers import try_handler
from doeff_vm import Err, WithHandler

from doeff import (
    EffectBase,
    Pass,
    Try,
    do,
    run,
)
from doeff import handler as _install_raw_handler


@dataclass(frozen=True, kw_only=True)
class Ping(EffectBase):
    label: str


# ---------------------------------------------------------------------------
# Non-@do (plain function) handlers
# ---------------------------------------------------------------------------


def _plain_crashing_handler(effect, k):
    """Plain function handler that raises on Ping."""
    if isinstance(effect, Ping):
        raise ValueError("handler crashed")
    return Pass(effect, k)


def _plain_key_error_handler(effect, k):
    """Plain function handler that raises KeyError on Ping."""
    if isinstance(effect, Ping):
        config = {}
        _ = config[effect.label]  # KeyError
    return Pass(effect, k)


# ---------------------------------------------------------------------------
# @do (generator) handlers — baseline: these should already work
# ---------------------------------------------------------------------------


@do
def _do_crashing_handler(effect, k):
    if isinstance(effect, Ping):
        raise ValueError("handler body crashed")
        yield  # unreachable, makes it a generator
    yield Pass()


# ---------------------------------------------------------------------------
# Test: Try effect catches non-@do handler exceptions
# ---------------------------------------------------------------------------


def test_plain_handler_error_caught_by_try_effect():
    """Non-@do handler raises ValueError -> Try should return Err(ValueError)."""

    @do
    def body():
        result = yield Ping(label="x")
        return result

    @do
    def program():
        result = yield Try(_install_raw_handler(_plain_crashing_handler)(body()))
        return result

    result = run(try_handler(program()))
    assert isinstance(result, Err), f"Expected Err, got {result!r}"
    assert isinstance(result.error, ValueError)
    assert "handler crashed" in str(result.error)


def test_plain_handler_key_error_caught_by_try_effect():
    """Non-@do handler raises KeyError -> Try should return Err(KeyError)."""

    @do
    def body():
        result = yield Ping(label="missing_key")
        return result

    @do
    def program():
        result = yield Try(_install_raw_handler(_plain_key_error_handler)(body()))
        return result

    result = run(try_handler(program()))
    assert isinstance(result, Err), f"Expected Err, got {result!r}"
    assert isinstance(result.error, KeyError)


# ---------------------------------------------------------------------------
# Test: try/except catches non-@do handler exceptions
# ---------------------------------------------------------------------------


def test_plain_handler_error_caught_by_try_except():
    """Non-@do handler raises -> try/except around WithHandler should catch."""

    @do
    def body():
        result = yield Ping(label="x")
        return result

    @do
    def program():
        try:
            result = yield _install_raw_handler(_plain_crashing_handler)(body())
            return result
        except ValueError as e:
            return f"caught: {e}"

    result = run(program())
    assert result == "caught: handler crashed"


# ---------------------------------------------------------------------------
# Test: @do handler errors — baseline (should already pass)
# ---------------------------------------------------------------------------


def test_do_handler_body_error_caught_by_try_effect():
    """@do handler raises in body -> Try should return Err. (Baseline)"""

    @do
    def body():
        result = yield Ping(label="x")
        return result

    @do
    def program():
        result = yield Try(_install_raw_handler(_do_crashing_handler)(body()))
        return result

    result = run(try_handler(program()))
    assert isinstance(result, Err), f"Expected Err, got {result!r}"
    assert isinstance(result.error, ValueError)
    assert "handler body crashed" in str(result.error)


def test_do_handler_body_error_caught_by_try_except():
    """@do handler raises in body -> try/except should catch. (Baseline)"""

    @do
    def body():
        result = yield Ping(label="x")
        return result

    @do
    def program():
        try:
            result = yield _install_raw_handler(_do_crashing_handler)(body())
            return result
        except ValueError as e:
            return f"caught: {e}"

    result = run(program())
    assert result == "caught: handler body crashed"


# ---------------------------------------------------------------------------
# Test: error preserves original exception type (not wrapped in RuntimeError)
# ---------------------------------------------------------------------------


def test_plain_handler_error_preserves_exception_type():
    """The original exception type should be preserved, not wrapped in RuntimeError."""

    def type_error_handler(effect, k):
        if isinstance(effect, Ping):
            raise TypeError("wrong type")
        return Pass(effect, k)

    @do
    def body():
        result = yield Ping(label="x")
        return result

    @do
    def program():
        result = yield Try(_install_raw_handler(type_error_handler)(body()))
        return result

    result = run(try_handler(program()))
    assert isinstance(result, Err)
    assert isinstance(result.error, TypeError), (
        f"Expected TypeError, got {type(result.error).__name__}"
    )
    assert "wrong type" in str(result.error)


# ---------------------------------------------------------------------------
# Test: handler error during Pass delegation
# ---------------------------------------------------------------------------


def test_pass_delegation_handler_error_caught():
    """If a handler raises during Pass delegation, it should be catchable."""

    def inner_handler(effect, k):
        # Doesn't handle Ping, passes to outer
        return Pass(effect, k)

    def outer_handler(effect, k):
        if isinstance(effect, Ping):
            raise RuntimeError("outer handler failed")
        return Pass(effect, k)

    @do
    def body():
        result = yield Ping(label="x")
        return result

    @do
    def program():
        inner = _install_raw_handler(inner_handler)(body())
        result = yield Try(_install_raw_handler(outer_handler)(inner))
        return result

    result = run(try_handler(program()))
    assert isinstance(result, Err), f"Expected Err, got {result!r}"
    assert isinstance(result.error, RuntimeError)
    assert "outer handler failed" in str(result.error)


# ---------------------------------------------------------------------------
# Tests (#492): deferred @do handler-construction failure must not leave a
# stale perform-site continuation behind.
#
# A wrong-arity @do handler matters because the @do wrapper returns
# Expand(Apply(Pure(thunk), [])) immediately; the arity TypeError only fires
# later, inside eval_apply, when the thunk calls fn(*args) — i.e. outside the
# synchronous dispatch recovery. Pre-fix, the perform-site k handle leaked
# into a VM-global slot, was adopted by the NEXT @do call's Expand frame, and
# a later unrelated exception was delivered INTO the long-abandoned victim
# continuation — whose return value was then silently substituted as the
# unrelated program's result (the 2026-07-07 exit-0 incident class).
# ---------------------------------------------------------------------------


@do
def _do_wrong_arity_handler(effect):  # wrong arity: handlers take (effect, k)
    yield None


def test_do_handler_wrong_arity_type_error_catchable():
    """The deferred arity TypeError routes to the perform site's dynamic scope
    and is catchable around WithHandler — same as the plain-handler case."""

    @do
    def victim():
        yield Ping(label="x")
        return "victim: no exception"

    @do
    def program():
        try:
            result = yield WithHandler(_do_wrong_arity_handler, victim())
            return result
        except TypeError as e:
            return f"caught: {e}"

    result = run(program())
    assert result.startswith("caught: "), f"Expected caught TypeError, got {result!r}"
    assert "positional argument" in result


def test_stale_handle_not_adopted_by_next_program():
    """#492 repro: after catching the arity TypeError, a later unrelated
    uncaught RuntimeError must propagate out of run() — NOT be delivered into
    the abandoned victim continuation (which would substitute the victim's
    return value as the crasher's result)."""

    caught_type_errors: list[TypeError] = []

    @do
    def victim():
        try:
            yield Ping(label="x")
            return "victim: no exception"
        except RuntimeError as e:
            return f"VICTIM CAUGHT WRONG-SCOPE EXCEPTION: {e}"

    @do
    def crasher():
        if True:
            raise RuntimeError("crash in unrelated subprogram")
        yield  # unreachable, makes it a generator

    @do
    def main():
        try:
            yield WithHandler(_do_wrong_arity_handler, victim())
        except TypeError as e:
            caught_type_errors.append(e)
        r = yield crasher()
        return ("crasher returned", r)

    with pytest.raises(RuntimeError, match="crash in unrelated subprogram"):
        run(main())

    assert len(caught_type_errors) == 1, (
        "the deferred handler-construction arity TypeError must surface "
        f"exactly once at the dispatch's dynamic scope, got {caught_type_errors!r}"
    )
