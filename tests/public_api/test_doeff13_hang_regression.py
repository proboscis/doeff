"""doeff-13 hang regression tests (RED stage).

These tests capture the known hanging behavior when ``@do``-decorated handlers
are used with ``WithHandler``.  The KPC auto-unwrap path re-enters itself and
spins forever; a 3.0 s watchdog budget bounds every scenario so the suite
always terminates.

Post-fix expectation: all tests PASS within the budget.
Pre-fix expectation:  ``@do``-handler tests FAIL (timeout) while the negative-
                      control plain-handler test PASSES.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from doeff import (
    Delegate,
    Effect,
    EffectBase,
    Pass,
    Resume,
    WithHandler,
    default_handlers,
    do,
    run,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WATCHDOG_TIMEOUT: float = 3.0
"""Hard budget shared by every test.  Mirrors the baseline evidence timeout."""


class _CustomEffect(EffectBase):
    """Minimal custom effect for handler protocol verification."""

    def __init__(self, value: object) -> None:
        self.value = value


def _prog(gen_factory):
    """Wrap a bare generator factory into a DoExpr via ``@do``."""

    @do
    def _wrapped():
        return (yield from gen_factory())

    return _wrapped()


def _run_with_watchdog(
    program_factory,
    *,
    timeout: float = WATCHDOG_TIMEOUT,
    store: dict[str, object] | None = None,
) -> Any:
    """Execute *program_factory()* on a daemon thread with a hard timeout.

    Mirrors the watchdog strategy in ``tests/effects/test_external_promise.py``.
    If the thread is still alive after *timeout* seconds the assertion fires
    immediately — the test fails rather than hanging the suite.
    """
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = run(program_factory(), handlers=default_handlers(), store=store)
        except BaseException as exc:
            error["value"] = exc

    thread: threading.Thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        pytest.fail(
            f"Program did not complete within {timeout}s — "
            "likely infinite re-entry in @do handler / KPC path (doeff-13)"
        )

    if "value" in error:
        raise error["value"]

    return result["value"]


# ---------------------------------------------------------------------------
# RED tests — expected to FAIL before the fix lands
# ---------------------------------------------------------------------------


class TestDoeff13HangRegression:
    """Hang-regression suite for doeff-13.

    Every scenario uses a ``@do``-decorated handler pushed via ``WithHandler``.
    Before the fix these hang indefinitely; the watchdog converts each hang
    into a bounded, deterministic failure.
    """

    def test_do_handler_path_completes_within_3s(self) -> None:
        """A @do-decorated handler that plain-returns should NOT hang.

        Post-fix behavior: @do handlers execute as Kleisli handlers and can
        plain-return values without hanging.
        """

        @do
        def handler(effect: Effect, _k):
            if isinstance(effect, _CustomEffect):
                return f"wrapped:{effect.value}"
            yield Delegate()

        def body():
            result = yield _CustomEffect("x")
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        run_result = _run_with_watchdog(lambda: _prog(main))
        assert run_result.value == "wrapped:x"

    def test_nested_do_handler_path_completes_within_3s(self) -> None:
        """Two nested @do-decorated handlers should NOT hang.

        The outer handler delegates unknown effects; the inner handler
        intercepts ``_CustomEffect``.  Pre-fix both layers trigger re-entry.

        Nested @do handlers should no longer hang and should produce the
        delegated inner-handler value.
        """

        @do
        def inner_handler(effect: Effect, _k):
            if isinstance(effect, _CustomEffect):
                return f"inner:{effect.value}"
            yield Delegate()

        @do
        def outer_handler(effect: Effect, _k):
            # Outer handler never intercepts _CustomEffect — always delegates.
            yield Delegate()

        def body():
            result = yield _CustomEffect("hello")
            return result

        def with_inner():
            result = yield WithHandler(handler=inner_handler, expr=_prog(body))
            return result

        def main():
            result = yield WithHandler(handler=outer_handler, expr=_prog(with_inner))
            return result

        run_result = _run_with_watchdog(lambda: _prog(main))
        assert run_result.value == "inner:hello"

    def test_no_infinite_reentry_on_custom_handler(self) -> None:
        """A @do handler that yields an effect INSIDE the handler must not loop.

        The handler intercepts ``_CustomEffect`` and internally yields a
        ``Get`` (state read) — an operation that requires the default
        handlers.  If the KPC path re-enters the handler stack this becomes
        an infinite loop.

        Post-fix: this path must complete and return the state value.
        """
        from doeff import Get

        @do
        def handler(effect: Effect, _k):
            if isinstance(effect, _CustomEffect):
                # Yield an effect *inside* the handler body — this is the
                # pattern most likely to trigger infinite KPC re-entry.
                state_val: object = yield Get("sentinel")
                return f"got:{effect.value}:{state_val}"
            yield Pass()

        def body():
            result = yield _CustomEffect("x")
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        run_result = _run_with_watchdog(lambda: _prog(main), store={"sentinel": "fallback"})
        assert run_result.value == "got:x:fallback"


# ---------------------------------------------------------------------------
# Negative control — MUST pass even before the fix
# ---------------------------------------------------------------------------


class TestDoeff13NegativeControl:
    """Explicit ``Resume`` handler path remains fast under watchdog budget."""

    def test_plain_handler_with_resume_completes_quickly(self) -> None:
        """A bare-generator handler using ``Resume`` must finish well within
        the watchdog budget.  This validates that the watchdog itself is not
        the source of false failures.

        Expected: PASS both before and after fix.
        """

        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, f"wrapped:{effect.value}"))
            yield Delegate()

        def body():
            result = yield _CustomEffect("x")
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        run_result = _run_with_watchdog(lambda: _prog(main))
        assert run_result.value == "wrapped:x"
