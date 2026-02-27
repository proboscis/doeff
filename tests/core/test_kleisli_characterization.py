"""Characterization tests for handler dispatch — locks current behavior before Kleisli refactor.

These tests capture CURRENT behavior as a safety net. All must PASS immediately
(no implementation changes needed). Written for VM-KLEISLI-PHASE1 Step 1.
"""

from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Effect,
    EffectBase,
    Get,
    Pass,
    Put,
    Resume,
    Tell,
    WithHandler,
    default_handlers,
    do,
    run,
)

# ---------------------------------------------------------------------------
# Custom effects for testing
# ---------------------------------------------------------------------------


class Ping(EffectBase):
    """Simple custom effect with a payload string."""

    def __init__(self, payload: str) -> None:
        super().__init__()
        self.payload = payload


class Pong(EffectBase):
    """Second custom effect for multi-effect handler tests."""

    def __init__(self, payload: str) -> None:
        super().__init__()
        self.payload = payload


# ---------------------------------------------------------------------------
# 1a. Plain generator handler (current behavior — works)
# ---------------------------------------------------------------------------


class TestPlainGeneratorHandler:
    def test_resume_returns_value_to_body(self) -> None:
        """Plain handler intercepts effect, Resume returns value."""

        def handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"pong:{effect.payload}"))
            yield Pass()

        @do
        def body():
            result = yield Ping("hello")
            return result

        r = run(WithHandler(handler, body()), handlers=default_handlers())
        assert r.value == "pong:hello"

    def test_delegate_passes_to_outer(self) -> None:
        """Inner handler delegates, outer handler catches."""

        def inner_handler(effect, k):
            yield Pass()

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "from-outer"))
            yield Pass()

        @do
        def body():
            return (yield Ping("test"))

        prog = WithHandler(outer_handler, WithHandler(inner_handler, body()))
        r = run(prog, handlers=default_handlers())
        assert r.value == "from-outer"

    def test_handler_can_yield_effects(self) -> None:
        """Handler body itself yields Get/Put effects."""

        def handler(effect, k):
            if isinstance(effect, Ping):
                val = yield Get("key")
                return (yield Resume(k, f"got:{val}"))
            yield Pass()

        @do
        def body():
            return (yield Ping("req"))

        r = run(WithHandler(handler, body()), handlers=default_handlers(), store={"key": "magic"})
        assert r.value == "got:magic"

    def test_multiple_effects_in_body(self) -> None:
        """Handler handles multiple different effects from same body."""

        def handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"ping:{effect.payload}"))
            if isinstance(effect, Pong):
                return (yield Resume(k, f"pong:{effect.payload}"))
            yield Pass()

        @do
        def body():
            a = yield Ping("A")
            b = yield Pong("B")
            return f"{a}+{b}"

        r = run(WithHandler(handler, body()), handlers=default_handlers())
        assert r.value == "ping:A+pong:B"


# ---------------------------------------------------------------------------
# 1b. Rust built-in handlers (current behavior — works)
# ---------------------------------------------------------------------------


class TestRustBuiltinHandlers:
    def test_state_get_put(self) -> None:
        """@do program with Put/Get via default_handlers."""

        @do
        def body():
            yield Put("x", 10)
            val = yield Get("x")
            return val

        r = run(body(), handlers=default_handlers())
        assert r.value == 10

    def test_reader_ask(self) -> None:
        """@do program with Ask via default_handlers."""

        @do
        def body():
            val = yield Ask("key")
            return val

        r = run(body(), handlers=default_handlers(), env={"key": "hello"})
        assert r.value == "hello"

    def test_writer_tell(self) -> None:
        """@do program with Tell via default_handlers."""

        @do
        def body():
            yield Tell("log-entry")
            return "done"

        r = run(body(), handlers=default_handlers())
        assert r.value == "done"
        assert "log-entry" in r.log

    def test_state_with_custom_handler(self) -> None:
        """Rust state + custom Python WithHandler coexist."""

        def custom_handler(effect, k):
            if isinstance(effect, Ping):
                # Handler uses Get (built-in) inside its body
                current = yield Get("counter")
                yield Put("counter", current + 1)
                return (yield Resume(k, f"count:{current + 1}"))
            yield Pass()

        @do
        def body():
            return (yield Ping("inc"))

        r = run(
            WithHandler(custom_handler, body()),
            handlers=default_handlers(),
            store={"counter": 0},
        )
        assert r.value == "count:1"


# ---------------------------------------------------------------------------
# 1c. @do handler (current behavior — TypeError)
# ---------------------------------------------------------------------------


class TestDoHandlerPreKleisli:
    """@do handlers currently fail with TypeError. Mark with pre_kleisli_behavior."""

    @pytest.mark.pre_kleisli_behavior
    def test_do_handler_returns_type_error(self) -> None:
        """@do handler → TypeError 'must return a generator'."""

        @do
        def handler(effect: Effect, k):
            return f"handled:{effect}"

        @do
        def body():
            return (yield Ping("test"))

        result = run(WithHandler(handler, body()), handlers=default_handlers())
        assert result.is_err()
        assert isinstance(result.error, TypeError)
        assert (
            "must return a generator" in str(result.error).lower()
            or "generator" in str(result.error).lower()
        )

    @pytest.mark.pre_kleisli_behavior
    def test_do_handler_with_effects_returns_type_error(self) -> None:
        """@do handler that yields effects also fails with TypeError."""

        @do
        def handler(effect: Effect, k):
            val = yield Get("x")
            return f"{val}:{effect}"

        @do
        def body():
            return (yield Ping("test"))

        result = run(WithHandler(handler, body()), handlers=default_handlers(), store={"x": "env"})
        assert result.is_err()
        assert isinstance(result.error, TypeError)
        assert (
            "must return a generator" in str(result.error).lower()
            or "generator" in str(result.error).lower()
        )


# ---------------------------------------------------------------------------
# 1d. Handler identity / self-exclusion (current behavior — works)
# ---------------------------------------------------------------------------


class TestHandlerIdentity:
    def test_handler_does_not_handle_own_effects(self) -> None:
        """OCaml-style self-exclusion: handler body effects skip own handler."""

        def inner_handler(effect, k):
            if isinstance(effect, Ping):
                # This Ping from the handler body should NOT be caught by inner_handler itself
                relay = yield Ping(f"relayed:{effect.payload}")
                return (yield Resume(k, relay))
            yield Pass()

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, f"outer-saw:{effect.payload}"))
            yield Pass()

        @do
        def body():
            return (yield Ping("hello"))

        prog = WithHandler(outer_handler, WithHandler(inner_handler, body()))
        r = run(prog, handlers=default_handlers())
        assert r.value == "outer-saw:relayed:hello"


# ---------------------------------------------------------------------------
# 1e. Nested WithHandler (current behavior — works)
# ---------------------------------------------------------------------------


class TestNestedWithHandler:
    def test_inner_handler_takes_priority(self) -> None:
        """Innermost matching handler wins."""

        def outer_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "from-outer"))
            yield Pass()

        def inner_handler(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "from-inner"))
            yield Pass()

        @do
        def body():
            return (yield Ping("test"))

        prog = WithHandler(outer_handler, WithHandler(inner_handler, body()))
        r = run(prog, handlers=default_handlers())
        assert r.value == "from-inner"

    def test_delegation_chain(self) -> None:
        """Effects delegate through handlers until one matches."""

        def handler_a(effect, k):
            if isinstance(effect, Ping):
                return (yield Resume(k, "handler-a"))
            yield Pass()

        def handler_b(effect, k):
            # Delegates everything
            yield Pass()

        def handler_c(effect, k):
            # Delegates everything
            yield Pass()

        @do
        def body():
            return (yield Ping("find-me"))

        # handler_a is outermost, handler_c is innermost
        # body → handler_c (delegates) → handler_b (delegates) → handler_a (catches Ping)
        prog = WithHandler(handler_a, WithHandler(handler_b, WithHandler(handler_c, body())))
        r = run(prog, handlers=default_handlers())
        assert r.value == "handler-a"
