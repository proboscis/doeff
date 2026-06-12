"""SPEC-TYPES-001 §11.2 — Handler Authoring Protocol Tests (HP-01 through HP-10).

All tests exercise the public handler-authoring API end-to-end through run().
"""

from __future__ import annotations

from typing import Any

from doeff import (
    Ask,
    Effect,
    EffectBase,
    Get,
    Pass,
    Pure,
    Put,
    Resume,
    do,
)
from doeff import handler as _install_raw_handler
from tests._run_helpers import run_with_defaults


class _CustomEffect(EffectBase):
    """Test effect for handler protocol verification."""

    def __init__(self, value: Any) -> None:
        super().__init__()
        self.value = value


def _prog(gen_factory):
    """Wrap a generator factory into a DoExpr via @do."""

    @do
    def _wrapped():
        return (yield from gen_factory())

    return _wrapped()


# ---------------------------------------------------------------------------
# HP-01: Custom handler with Resume(k, value)
# ---------------------------------------------------------------------------


class TestHP01BasicHandler:
    def test_handler_intercepts_and_resumes(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 2))
            else:
                yield effect

        def body():
            result = yield _CustomEffect(21)
            return result

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == 42


# ---------------------------------------------------------------------------
# HP-02: Handler receives original effect object
# ---------------------------------------------------------------------------


class TestHP02EffectAttributes:
    def test_handler_reads_effect_attributes(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, f"got:{effect.value}"))
            else:
                yield effect

        def body():
            result = yield _CustomEffect("hello")
            return result

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == "got:hello"


# ---------------------------------------------------------------------------
# HP-03: Handler post-processes resume result
# ---------------------------------------------------------------------------


class TestHP03PostProcess:
    def test_handler_transforms_resume_result(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                resume_value = yield Resume(k, effect.value)
                return resume_value * 3
            else:
                yield effect

        def body():
            x = yield _CustomEffect(10)
            return x + 5  # body returns 15

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == 45  # handler gets 15, returns 15*3


class TestHP03BReturnEffect:
    pass


class TestHP03CReturnRawDoExpr:
    def test_handler_returning_doexpr_after_resume_is_not_auto_evaluated(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                resume_value = yield Resume(k, effect.value)
                return Pure(resume_value * 3)
            else:
                yield effect

        def body():
            x = yield _CustomEffect(10)
            return x + 5

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert isinstance(result.value, Pure)
        assert result.value.value == 45


# ---------------------------------------------------------------------------
# HP-04: Handler abandons continuation (early return)
# ---------------------------------------------------------------------------


class TestHP04AbandonContinuation:
    pass


# ---------------------------------------------------------------------------
# HP-05: Delegate forwards to outer handler
# ---------------------------------------------------------------------------


class TestHP05Delegate:
    def test_inner_delegates_to_outer(self) -> None:
        @do
        def outer_handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value + 100))
            else:
                yield effect

        @do
        def inner_handler(effect: Effect, k):
            # always delegate
            yield Pass(effect, k)

        def body():
            result = yield _CustomEffect(5)
            return result

        def inner():
            result = yield _install_raw_handler(inner_handler)(_prog(body))
            return result

        def main():
            result = yield _install_raw_handler(outer_handler)(_prog(inner))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == 105


# ---------------------------------------------------------------------------
# HP-06: Nested WithHandler (inner intercepts first)
# ---------------------------------------------------------------------------


class TestHP06NestedHandlers:
    def test_inner_handler_intercepts_before_outer(self) -> None:
        @do
        def inner_handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value + 100))
            else:
                yield effect

        @do
        def outer_handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 2))
            else:
                yield effect

        def body():
            val = yield _CustomEffect(5)  # inner: 5 + 100 = 105
            return val

        def with_inner():
            result = yield _install_raw_handler(inner_handler)(_prog(body))
            return result

        def main():
            # outer doesn't get to handle CustomEffect — inner already did
            result = yield _install_raw_handler(outer_handler)(_prog(with_inner))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == 105  # inner handler won

    def test_delegated_then_outer_handles(self) -> None:
        @do
        def inner_handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect) and effect.value < 10:
                return (yield Resume(k, effect.value + 100))
            else:
                yield Pass(effect, k)

        @do
        def outer_handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 2))
            else:
                yield Pass(effect, k)

        def body():
            a = yield _CustomEffect(5)  # inner: 5 + 100 = 105
            b = yield _CustomEffect(50)  # inner delegates, outer: 50 * 2 = 100
            return (a, b)

        def with_inner():
            result = yield _install_raw_handler(inner_handler)(_prog(body))
            return result

        def main():
            result = yield _install_raw_handler(outer_handler)(_prog(with_inner))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == (105, 100)


# ---------------------------------------------------------------------------
# HP-07: Stateful handler (closure state)
# ---------------------------------------------------------------------------


class TestHP07StatefulHandler:
    def test_closure_state_accumulates(self) -> None:
        def make_counting_handler():
            state = {"count": 0}

            @do
            def handler(effect: Effect, k):
                if isinstance(effect, _CustomEffect):
                    state["count"] += 1
                    return (yield Resume(k, state["count"]))
                else:
                    yield effect

            return handler, state

        handler, state = make_counting_handler()

        def body():
            a = yield _CustomEffect("x")
            b = yield _CustomEffect("y")
            c = yield _CustomEffect("z")
            return [a, b, c]

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == [1, 2, 3]
        assert state["count"] == 3


# ---------------------------------------------------------------------------
# HP-08: WithHandler(h, body) keyword syntax
# ---------------------------------------------------------------------------


class TestHP08WithHandlerKeywords:
    def test_keyword_args(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 3))
            else:
                yield effect

        def body():
            result = yield _CustomEffect(10)
            return result

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == 30


# ---------------------------------------------------------------------------
# HP-09: Multiple effects in one body
# ---------------------------------------------------------------------------


class TestHP09MultipleEffects:
    def test_handler_invoked_for_each_effect(self) -> None:
        invocations = []

        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                invocations.append(effect.value)
                return (yield Resume(k, effect.value))
            else:
                yield effect

        def body():
            yield _CustomEffect("a")
            yield _CustomEffect("b")
            yield _CustomEffect("c")
            return "done"

        def main():
            result = yield _install_raw_handler(handler)(_prog(body))
            return result

        result = run_with_defaults(_prog(main))
        assert result.value == "done"
        assert invocations == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# HP-10: Custom handler coexists with built-in handlers
# ---------------------------------------------------------------------------


class TestHP10CoexistWithBuiltins:
    def test_custom_handler_with_state_reader_writer(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value))
            else:
                yield Pass(effect, k)

        @do
        def body():
            # Use built-in effects
            env_val = yield Ask("api_key")
            state_val = yield Get("counter")
            yield Put("counter", state_val + 1)
            # Use custom effect
            custom_val = yield _CustomEffect(env_val)
            return f"{custom_val}:{state_val}"

        def main():
            result = yield _install_raw_handler(handler)(body())
            return result

        result = run_with_defaults(_prog(main), env={"api_key": "secret"}, store={"counter": 10})
        assert result.value == "secret:10"


class TestHP11DoDecoratedHandler:
    pass
