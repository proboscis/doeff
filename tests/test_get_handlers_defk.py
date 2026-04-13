"""Reproducer: GetHandlers(k) does not return handlers installed via defk + WithHandler.

When handlers are installed via _compose_handlers (WithHandler chain returned as
a Program value from a defk body), GetHandlers(k) should still see them in the
fiber chain. Currently it returns 0 inner handlers.

This breaks Spawn handler inheritance: scheduler uses get_inner_handlers(k) to
capture handlers for spawned tasks, but misses handlers installed via defk.

See: ISSUE-INF-023 (proboscis-ema)
"""
from __future__ import annotations

import pytest

from doeff import (
    EffectBase,
    EffectGenerator,
    Pass,
    Resume,
    WithHandler,
    do,
    run,
)
from doeff.handler_utils import get_inner_handlers
from doeff_core_effects import slog
from doeff_core_effects.handlers import (
    await_handler,
    slog_handler,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.scheduler import Spawn, Wait, Gather, scheduled
from doeff_vm import EffectBase as VMEffectBase, Ok, Err


# --- Effects ---

class FetchData(VMEffectBase):
    def __init__(self, key):
        self.key = key


class MarkerEffect(VMEffectBase):
    """Marker effect to test handler visibility."""
    pass


# --- Handlers ---

@do
def fetch_handler(effect, k):
    """Resolves FetchData effects."""
    if not isinstance(effect, FetchData):
        yield Pass(effect, k)
        return
    return (yield Resume(k, f"data({effect.key})"))


@do
def marker_handler(effect, k):
    """Handler that we check is visible via GetHandlers."""
    if not isinstance(effect, MarkerEffect):
        yield Pass(effect, k)
        return
    return (yield Resume(k, "marked"))


# --- Helper ---

def _compose(program, *handlers):
    wrapped = program
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return wrapped


# --- Tests ---

class TestGetHandlersDirectNesting:
    """Control: handlers installed via direct WithHandler nesting."""

    def test_inner_handlers_visible(self):
        """get_inner_handlers sees handlers between body and catcher."""
        seen = {"handlers": None}

        @do
        def catching_handler(effect, k):
            if isinstance(effect, FetchData):
                hs = yield get_inner_handlers(k)
                seen["handlers"] = len(hs)
                return (yield Resume(k, "caught"))
            yield Pass(effect, k)

        @do
        def prog():
            return (yield FetchData("test"))

        # marker_handler is between prog and catching_handler
        composed = WithHandler(catching_handler,
                    WithHandler(marker_handler, prog()))
        result = run(scheduled(composed))
        assert result == "caught"
        assert seen["handlers"] == 1, f"Expected 1 inner handler (marker), got {seen['handlers']}"

    def test_spawn_inherits_handlers(self):
        """Spawned task can access handler installed via direct nesting."""
        @do
        def prog():
            @do
            def task():
                return (yield MarkerEffect())

            t = yield Spawn(task())
            return (yield Wait(t))

        composed = _compose(prog(),
            writer(), try_handler, state(), await_handler(),
            slog_handler(),
            marker_handler,
        )
        result = run(scheduled(composed))
        assert result == "marked"


class TestGetHandlersViaDefk:
    """Bug reproducer: handlers installed via defk + WithHandler Program."""

    def test_inner_handlers_visible_via_compose(self):
        """get_inner_handlers should see handlers from _compose, not just direct nesting."""
        seen = {"handlers": None}

        @do
        def catching_handler(effect, k):
            if isinstance(effect, FetchData):
                hs = yield get_inner_handlers(k)
                seen["handlers"] = len(hs)
                return (yield Resume(k, "caught"))
            yield Pass(effect, k)

        @do
        def prog():
            return (yield FetchData("test"))

        # Simulate defk pattern: compose handlers as a Program, then yield it
        @do
        def defk_body():
            composed = _compose(prog(), catching_handler, marker_handler)
            result = yield composed
            return result

        result = run(scheduled(defk_body()))
        assert result == "caught"
        # marker_handler should be visible between prog and catching_handler
        assert seen["handlers"] == 1, (
            f"Expected 1 inner handler (marker), got {seen['handlers']}. "
            "GetHandlers does not see handlers installed via WithHandler Program."
        )

    def test_spawn_inherits_handlers_via_compose(self):
        """Spawned task should access handlers from _compose_handlers pattern."""
        @do
        def prog():
            @do
            def task():
                return (yield MarkerEffect())

            t = yield Spawn(task())
            return (yield Wait(t))

        # Simulate interpreter pattern: defk composes handlers, runs program
        @do
        def defk_interpreter():
            composed = _compose(prog(),
                writer(), try_handler, state(), await_handler(),
                slog_handler(),
                marker_handler,
            )
            result = yield composed
            return result

        result = run(scheduled(defk_interpreter()))
        assert result == "marked", (
            "Spawned task cannot access marker_handler installed via defk + WithHandler. "
            "GetHandlers/Spawn inheritance does not walk the full fiber chain."
        )
