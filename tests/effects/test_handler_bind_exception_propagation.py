"""Exception propagation through handler `<-` (non-terminal delegate).

When a handler's clause body does `(<- x effect)` (Hy) or `value = yield effect`
(Python) — i.e. delegates to the outer handler — and the outer raises, the
exception MUST land at the yield point inside the inner handler's body so the
inner handler's `try/except` can catch it. This mirrors OCaml 5's effect
handler semantics: an unhandled exception in a handler body propagates back to
the perform site.

These regression tests lock the VM behavior. Until they pass, the only way to
get exceptions across `<-` chains is the `try/except + TransferThrow(k, exc)`
boilerplate in memo_handlers.py — which the user wants to eliminate.
"""

from __future__ import annotations

import pytest

from doeff import EffectBase, Pass, Resume, UnhandledEffect, do, run
from doeff import handler as _program_handler


class _MyEffect(EffectBase):
    """Plain effect class — no special handling needed."""


class _OtherEffect(EffectBase):
    """Effect raised inside a handler body when delegating outer raises."""


def _wrap(program, *handlers):
    """Wrap program with handlers (first handler is outermost)."""
    wrapped = program
    for h in reversed(handlers):
        wrapped = _program_handler(h)(wrapped)
    return wrapped


def test_outer_raise_lands_at_inner_handler_bind_site():
    """Inner handler `<-` delegates to outer; outer raises; inner catches.

    Architecture under test:

        program → yields _MyEffect()
            ↓ caught by inner_handler
        inner_handler:
            try:
                value = yield _MyEffect()    # delegate to outer
            except ValueError as e:
                resume(k, f"caught: {e}")    # ← must execute
            ↓ delegated, caught by outer_handler
        outer_handler:
            raise ValueError("from outer")    # uncaught in handler body
    """

    @do
    def outer_handler(effect, k):
        if isinstance(effect, _MyEffect):
            raise ValueError("from outer")
        yield Pass(effect, k)

    @do
    def inner_handler(effect, k):
        if isinstance(effect, _MyEffect):
            try:
                value = yield _MyEffect()  # `<-` style: delegate to outer
            except ValueError as exc:
                return (yield Resume(k, f"caught: {exc}"))
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def program():
        return (yield _MyEffect())

    wrapped = _wrap(program(), outer_handler, inner_handler)
    assert run(wrapped) == "caught: from outer"


def test_outer_unhandled_lands_at_inner_handler_bind_site():
    """Same as above but outer fall-through (UnhandledEffect) instead of explicit raise.

    The classic memo case: storage layer does `<- effect` to broadcast to outer.
    Outermost re-perform falls off (no further handler), VM emits UnhandledEffect.
    The inner storage layer's `try/except UnhandledEffect` should catch it WITHOUT
    needing TransferThrow boilerplate.
    """

    @do
    def inner_handler(effect, k):
        if isinstance(effect, _MyEffect):
            try:
                # Delegate _OtherEffect to outer — no outer handles _OtherEffect,
                # so UnhandledEffect is raised. We catch it locally.
                value = yield _OtherEffect()
            except UnhandledEffect:
                return (yield Resume(k, "fell-through"))
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def program():
        return (yield _MyEffect())

    wrapped = _wrap(program(), inner_handler)
    assert run(wrapped) == "fell-through"


def test_outer_raise_propagates_when_inner_does_not_catch():
    """Inner handler `<-` delegates; outer raises; inner does NOT catch.

    Exception should propagate up to the caller (the user program), not be
    silently swallowed.
    """

    @do
    def outer_handler(effect, k):
        if isinstance(effect, _MyEffect):
            raise ValueError("from outer")
        yield Pass(effect, k)

    @do
    def inner_handler(effect, k):
        if isinstance(effect, _MyEffect):
            value = yield _MyEffect()  # no try/except; let it propagate
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def program():
        return (yield _MyEffect())

    wrapped = _wrap(program(), outer_handler, inner_handler)
    with pytest.raises(ValueError, match="from outer"):
        run(wrapped)


def test_outer_unhandled_propagates_when_inner_does_not_catch():
    """Storage-layer pattern without explicit catch — exception must propagate.

    This is the case proboscis-ema's pydantic_serialize_handler hits today: it
    delegates via `<-` and doesn't catch. The exception should arrive at the
    next outer caller's `<-` site (or at the user program's yield site if no
    further outer caller exists).
    """

    @do
    def inner_handler(effect, k):
        if isinstance(effect, _MyEffect):
            value = yield _OtherEffect()  # no try/except
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def program():
        return (yield _MyEffect())

    wrapped = _wrap(program(), inner_handler)
    with pytest.raises(UnhandledEffect):
        run(wrapped)


def test_three_layer_bind_propagation_from_outermost():
    """Three handlers stacked. Outermost raises during `<-`. Innermost catches."""

    @do
    def layer_outer(effect, k):
        if isinstance(effect, _MyEffect):
            raise ValueError("layer3")
        yield Pass(effect, k)

    @do
    def layer_middle(effect, k):
        if isinstance(effect, _MyEffect):
            value = yield _MyEffect()  # delegate to outer; no catch
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def layer_inner(effect, k):
        if isinstance(effect, _MyEffect):
            try:
                value = yield _MyEffect()  # delegate; catch
            except ValueError as exc:
                return (yield Resume(k, f"caught at inner: {exc}"))
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def program():
        return (yield _MyEffect())

    wrapped = _wrap(program(), layer_outer, layer_middle, layer_inner)
    assert run(wrapped) == "caught at inner: layer3"


def test_unhandled_propagates_through_intermediate_bind_to_inner_catch():
    """The proboscis-ema bug repro: a middle handler with no try/except
    must transparently forward exceptions from its `<-` to its caller.

    Stack (outermost ← innermost):
        outer (memo_handler equiv)   — try/except + TransferThrow on `<- _OtherEffect`
        middle (pydantic equiv)      — `<- _MyEffect` with NO try/except
        inner (sessioned-memo equiv) — try/except UnhandledEffect on `<- _MyEffect`
        program — yields _MyEffect

    Trace:
        1. program yields _MyEffect → inner catches (closest match wins)
        2. inner does `<- _MyEffect` → middle catches
        3. middle does `<- _MyEffect` → outer catches
        4. outer does `<- _OtherEffect` → unhandled → outer's try/except
           catches, does TransferThrow(k_outer, UnhandledEffect)
        5. TransferThrow lands at middle's `<- _MyEffect` site (k_outer goes
           back to whoever called outer, which is middle)
        6. middle has NO try/except — exception must propagate to inner's
           `<- _MyEffect` site
        7. inner's try/except UnhandledEffect catches → Resume(k_inner, "inner caught")

    Step 6 is where the VM currently fails: the exception in middle's body
    doesn't propagate back to inner's `<-` site. middle's body just dies
    abnormally, and the exception bypasses inner.
    """
    from doeff import TransferThrow

    @do
    def handler_outer(effect, k):
        if isinstance(effect, _MyEffect):
            try:
                yield _OtherEffect()  # unhandled — raises UnhandledEffect
            except UnhandledEffect as exc:
                return (yield TransferThrow(k, exc))
            return (yield Resume(k, "unreachable"))
        yield Pass(effect, k)

    @do
    def handler_middle(effect, k):
        if isinstance(effect, _MyEffect):
            value = yield _MyEffect()  # delegate; NO try/except
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def handler_inner(effect, k):
        if isinstance(effect, _MyEffect):
            try:
                value = yield _MyEffect()  # delegate
            except UnhandledEffect:
                return (yield Resume(k, "inner caught"))
            return (yield Resume(k, value))
        yield Pass(effect, k)

    @do
    def program():
        return (yield _MyEffect())

    # Stack: outermost first. innermost is handler_inner (closest to program).
    wrapped = _wrap(program(), handler_outer, handler_middle, handler_inner)
    assert run(wrapped) == "inner caught"
