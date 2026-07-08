"""PR E: UnhandledEffect error message must include the handler chain names.

Before PR E, an Unhandled/NoMatchingHandler error said only
    ``RuntimeError: no handler found for effect: Ask (Ask('missing'))``
leaving the user guessing which handlers were in scope. The Rust VM
already captures the chain in ``__doeff_traceback__``, but that's only
visible via the explicit ``format_default`` renderer. The message itself
is what bubbles up to ``pytest``, ``logging``, and CI dashboards, so we
now format the chain names directly into the ``str(exception)`` surface.

Handler names follow the same convention the traceback renderer uses:
strip ``.<locals>.`` closure suffixes and drop the module prefix, so
``doeff_core_effects.handlers.lazy_ask.<locals>.handler`` becomes
``lazy_ask``.
"""

from __future__ import annotations

import pytest
from doeff_core_effects.handlers import lazy_ask, slog_handler

from doeff import Ask, Pass, UnhandledEffect, do, run
from doeff import handler as _install_raw_handler

# Module-level handlers so their qualnames don't include ``.<locals>.`` noise
# from the enclosing test class/function.


@do
def only_handler(effect, k):
    yield Pass(effect, k)


@do
def telemetry_handler(effect, k):
    yield Pass(effect, k)


@do
def inner_pass_through(effect, k):
    yield Pass(effect, k)


@do
def outer_pass_through(effect, k):
    yield Pass(effect, k)


def _extract_msg(exc: BaseException) -> str:
    return str(exc)


class TestUnhandledEffectMessageIncludesChain:
    def test_single_handler_chain(self):
        """A single Pass-through handler shows up by name when the effect
        falls off the top of the chain."""
        @do
        def prog():
            return (yield Ask("missing"))

        with pytest.raises(UnhandledEffect) as excinfo:
            run(_install_raw_handler(only_handler)(prog()))
        msg = _extract_msg(excinfo.value)
        assert "only_handler" in msg
        assert "handlers in scope" in msg

    def test_multiple_handlers_listed(self):
        """All installed handlers appear in the chain message."""
        @do
        def prog():
            return (yield Ask("missing"))

        # lazy_ask defaults to Pass-on-miss (PR D), so Ask bubbles through
        # telemetry_handler → slog_handler → lazy_ask before going Unhandled.
        composed = lazy_ask(env={})(slog_handler(_install_raw_handler(telemetry_handler)(prog())))
        with pytest.raises(UnhandledEffect) as excinfo:
            run(composed)
        msg = _extract_msg(excinfo.value)
        assert "telemetry_handler" in msg
        assert "slog_handler" in msg
        assert "lazy_ask" in msg
        assert "handlers in scope" in msg

    def test_pass_fallthrough_preserves_order(self):
        """Innermost handler listed first, outermost last."""
        @do
        def prog():
            return (yield Ask("X"))

        composed = _install_raw_handler(outer_pass_through)(_install_raw_handler(inner_pass_through)(prog()))
        with pytest.raises(UnhandledEffect) as excinfo:
            run(composed)
        msg = _extract_msg(excinfo.value)
        assert "inner_pass_through" in msg
        assert "outer_pass_through" in msg
        assert msg.index("inner_pass_through") < msg.index("outer_pass_through")
