"""SPEC-TYPES-001 §11.2 — Handler Authoring Protocol Tests (HP-01 through HP-10).

All tests exercise the public handler-authoring API end-to-end through run().
"""

from __future__ import annotations

from typing import Any

import pytest

from doeff import (
    Ask,
    Delegate,
    Effect,
    EffectBase,
    Get,
    Pass,
    Put,
    Resume,
    WithHandler,
    default_handlers,
    do,
    run,
)


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
        def handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 2))
            else:
                yield Delegate()

        def body():
            result = yield _CustomEffect(21)
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == 42


# ---------------------------------------------------------------------------
# HP-02: Handler receives original effect object
# ---------------------------------------------------------------------------


class TestHP02EffectAttributes:
    def test_handler_reads_effect_attributes(self) -> None:
        def handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, f"got:{effect.value}"))
            else:
                yield Delegate()

        def body():
            result = yield _CustomEffect("hello")
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == "got:hello"


# ---------------------------------------------------------------------------
# HP-03: Handler post-processes resume result
# ---------------------------------------------------------------------------


class TestHP03PostProcess:
    def test_handler_transforms_resume_result(self) -> None:
        def handler(effect, k):
            if isinstance(effect, _CustomEffect):
                resume_value = yield Resume(k, effect.value)
                return resume_value * 3
            else:
                yield Delegate()

        def body():
            x = yield _CustomEffect(10)
            return x + 5  # body returns 15

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == 45  # handler gets 15, returns 15*3


class TestHP03BReturnEffect:
    def test_handler_returning_effect_raises_typeerror(self) -> None:
        def handler(effect, _k):
            if isinstance(effect, _CustomEffect):
                return Ask("api_key")
            return Delegate()

        def body():
            result = yield _CustomEffect("unused")
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers(), env={"api_key": "secret"})
        assert result.is_err()
        assert isinstance(result.error, TypeError)
        assert "must return a generator" in str(result.error)
        assert "Did you forget 'yield'?" in str(result.error)


# ---------------------------------------------------------------------------
# HP-04: Handler abandons continuation (early return)
# ---------------------------------------------------------------------------


class TestHP04AbandonContinuation:
    def test_handler_short_circuits(self) -> None:
        def handler(effect, _k):
            if isinstance(effect, _CustomEffect):
                return effect.value * 10  # no Resume — abandon
            return Delegate()

        def body():
            yield _CustomEffect(7)
            return 9999  # never reached

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.is_err()
        assert isinstance(result.error, TypeError)
        assert "must return a generator" in str(result.error)
        assert "Did you forget 'yield'?" in str(result.error)


# ---------------------------------------------------------------------------
# HP-05: Delegate forwards to outer handler
# ---------------------------------------------------------------------------


class TestHP05Delegate:
    def test_inner_delegates_to_outer(self) -> None:
        def outer_handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value + 100))
            else:
                yield Delegate()

        def inner_handler(effect, k):
            # always delegate
            yield Pass()

        def body():
            result = yield _CustomEffect(5)
            return result

        def inner():
            result = yield WithHandler(handler=inner_handler, expr=_prog(body))
            return result

        def main():
            result = yield WithHandler(handler=outer_handler, expr=_prog(inner))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == 105


# ---------------------------------------------------------------------------
# HP-06: Nested WithHandler (inner intercepts first)
# ---------------------------------------------------------------------------


class TestHP06NestedHandlers:
    def test_inner_handler_intercepts_before_outer(self) -> None:
        def inner_handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value + 100))
            else:
                yield Delegate()

        def outer_handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 2))
            else:
                yield Delegate()

        def body():
            val = yield _CustomEffect(5)  # inner: 5 + 100 = 105
            return val

        def with_inner():
            result = yield WithHandler(handler=inner_handler, expr=_prog(body))
            return result

        def main():
            # outer doesn't get to handle CustomEffect — inner already did
            result = yield WithHandler(handler=outer_handler, expr=_prog(with_inner))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == 105  # inner handler won

    def test_delegated_then_outer_handles(self) -> None:
        def inner_handler(effect, k):
            if isinstance(effect, _CustomEffect) and effect.value < 10:
                return (yield Resume(k, effect.value + 100))
            else:
                yield Pass()

        def outer_handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 2))
            else:
                yield Pass()

        def body():
            a = yield _CustomEffect(5)  # inner: 5 + 100 = 105
            b = yield _CustomEffect(50)  # inner delegates, outer: 50 * 2 = 100
            return (a, b)

        def with_inner():
            result = yield WithHandler(handler=inner_handler, expr=_prog(body))
            return result

        def main():
            result = yield WithHandler(handler=outer_handler, expr=_prog(with_inner))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == (105, 100)


# ---------------------------------------------------------------------------
# HP-07: Stateful handler (closure state)
# ---------------------------------------------------------------------------


class TestHP07StatefulHandler:
    def test_closure_state_accumulates(self) -> None:
        def make_counting_handler():
            state = {"count": 0}

            def handler(effect, k):
                if isinstance(effect, _CustomEffect):
                    state["count"] += 1
                    return (yield Resume(k, state["count"]))
                else:
                    yield Delegate()

            return handler, state

        handler, state = make_counting_handler()

        def body():
            a = yield _CustomEffect("x")
            b = yield _CustomEffect("y")
            c = yield _CustomEffect("z")
            return [a, b, c]

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == [1, 2, 3]
        assert state["count"] == 3


# ---------------------------------------------------------------------------
# HP-08: WithHandler(handler=h, expr=body) keyword syntax
# ---------------------------------------------------------------------------


class TestHP08WithHandlerKeywords:
    def test_keyword_args(self) -> None:
        def handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value * 3))
            else:
                yield Delegate()

        def body():
            result = yield _CustomEffect(10)
            return result

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == 30


# ---------------------------------------------------------------------------
# HP-09: Multiple effects in one body
# ---------------------------------------------------------------------------


class TestHP09MultipleEffects:
    def test_handler_invoked_for_each_effect(self) -> None:
        invocations = []

        def handler(effect, k):
            if isinstance(effect, _CustomEffect):
                invocations.append(effect.value)
                return (yield Resume(k, effect.value))
            else:
                yield Delegate()

        def body():
            yield _CustomEffect("a")
            yield _CustomEffect("b")
            yield _CustomEffect("c")
            return "done"

        def main():
            result = yield WithHandler(handler=handler, expr=_prog(body))
            return result

        result = run(_prog(main), handlers=default_handlers())
        assert result.value == "done"
        assert invocations == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# HP-10: Custom handler coexists with built-in handlers
# ---------------------------------------------------------------------------


class TestHP10CoexistWithBuiltins:
    def test_custom_handler_with_state_reader_writer(self) -> None:
        def handler(effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, effect.value))
            else:
                yield Pass()

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
            result = yield WithHandler(handler=handler, expr=body())
            return result

        result = run(
            _prog(main),
            handlers=default_handlers(),
            env={"api_key": "secret"},
            store={"counter": 10},
        )
        assert result.value == "secret:10"


class TestHP11DoDecoratedHandler:
    def test_do_decorated_handler_without_effect_annotation_is_rejected(self) -> None:
        @do
        def bad_handler(effect, _k):
            if isinstance(effect, _CustomEffect):
                return f"wrapped:{effect.value}"
            yield Delegate()

        def body():
            result = yield _CustomEffect("x")
            return result

        with pytest.raises(
            TypeError, match=r"@do handler first parameter must be annotated as Effect"
        ):
            WithHandler(handler=bad_handler, expr=_prog(body))

    def test_doeff_vm_withhandler_applies_python_side_validation(self) -> None:
        import doeff_vm

        @do
        def bad_handler(effect, _k):
            if isinstance(effect, _CustomEffect):
                return f"wrapped:{effect.value}"
            yield Delegate()

        def body():
            result = yield _CustomEffect("x")
            return result

        with pytest.raises(
            TypeError, match=r"@do handler first parameter must be annotated as Effect"
        ):
            doeff_vm.WithHandler(bad_handler, _prog(body))

    def test_run_rejects_unannotated_do_handler_in_handlers_list(self) -> None:
        @do
        def bad_handler(effect, _k):
            if isinstance(effect, _CustomEffect):
                return f"wrapped:{effect.value}"
            yield Delegate()

        def body():
            result = yield _CustomEffect("x")
            return result

        with pytest.raises(
            TypeError, match=r"@do handler first parameter must be annotated as Effect"
        ):
            run(_prog(body), handlers=[bad_handler])

    def test_direct_extension_submodule_also_rejects_unannotated_do_handler(self) -> None:
        import asyncio
        import importlib

        sub = importlib.import_module("doeff_vm.doeff_vm")

        @do
        def bad_handler(effect, _k):
            if isinstance(effect, _CustomEffect):
                return f"wrapped:{effect.value}"
            yield Delegate()

        def body():
            result = yield _CustomEffect("x")
            return result

        with pytest.raises(
            TypeError, match=r"@do handler first parameter must be annotated as Effect"
        ):
            sub.WithHandler(bad_handler, _prog(body))

        with pytest.raises(
            TypeError, match=r"@do handler first parameter must be annotated as Effect"
        ):
            sub.run(_prog(body), handlers=[bad_handler])

        with pytest.raises(
            TypeError, match=r"@do handler first parameter must be annotated as Effect"
        ):
            asyncio.run(sub.async_run(_prog(body), handlers=[bad_handler]))

    def test_do_decorated_handler_with_resume(self) -> None:
        @do
        def handler(effect: Effect, k):
            if isinstance(effect, _CustomEffect):
                return (yield Resume(k, f"wrapped:{effect.value}"))
            yield Pass()

        @do
        def body():
            result = yield _CustomEffect("x")
            return result

        result = run(
            WithHandler(handler, body()),
            handlers=default_handlers(),
        )
        assert result.value == "wrapped:x"
