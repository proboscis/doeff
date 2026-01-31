"""TDD tests for the handler frame system.

These tests define expected behavior for the WithHandler effect and related types.
Tests are expected to FAIL until the implementation is completed in ISSUE-CORE-464.

Test categories:
1. WithHandler creates HandlerFrame in K
2. Handler receives effect and returns ContinueValue
3. Handler can yield effect (bubbles to outer)
4. Nested WithHandler - innermost receives first
5. Handler forwarding skips current handler
6. ResumeK switches continuation
7. Outermost handler can use primitives
8. Unhandled effect raises error
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from doeff import do
from doeff._types_internal import EffectBase
from doeff._vendor import FrozenDict
from doeff.cesk.frames import ContinueError, ContinueValue, FrameResult, Kontinuation
from doeff.cesk.handler_frame import (
    Handler,
    HandlerContext,
    HandlerFrame,
    HandlerResultFrame,
    ResumeK,
    WithHandler,
)
from doeff.cesk.types import Environment, Store
from doeff.program import Program


@dataclass(frozen=True, kw_only=True)
class DummyEffect(EffectBase):
    value: int


@dataclass(frozen=True, kw_only=True)
class AnotherDummyEffect(EffectBase):
    message: str


class TestHandlerContext:
    def test_handler_context_holds_store_env_and_k(self) -> None:
        store: Store = {"key": "value"}
        env: Environment = FrozenDict({"env_key": "env_value"})
        k: Kontinuation = []

        ctx = HandlerContext(
            store=store, env=env, delimited_k=k, handler_depth=0
        )

        assert ctx.store == store
        assert ctx.env == env
        assert ctx.delimited_k == k
        assert ctx.handler_depth == 0

    def test_handler_context_tracks_depth(self) -> None:
        ctx = HandlerContext(
            store={}, env=FrozenDict(), delimited_k=[], handler_depth=3
        )
        assert ctx.handler_depth == 3


class TestResumeK:
    def test_resume_k_holds_continuation_and_value(self) -> None:
        k: Kontinuation = []
        result = ResumeK(k=k, value=42)

        assert result.k == k
        assert result.value == 42
        assert result.env is None
        assert result.store is None

    def test_resume_k_with_custom_env_and_store(self) -> None:
        k: Kontinuation = []
        env: Environment = FrozenDict({"x": 1})
        store: Store = {"y": 2}

        result = ResumeK(k=k, value="test", env=env, store=store)

        assert result.value == "test"
        assert result.env == env
        assert result.store == store


class TestHandlerFrame:
    def test_handler_frame_on_value_passes_through(self) -> None:
        def dummy_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            return Program.pure(ContinueValue(value=None, env=ctx.env, store=ctx.store, k=[]))

        env: Environment = FrozenDict({"original": "env"})
        current_env: Environment = FrozenDict({"current": "env"})
        store: Store = {"state": 1}
        k: Kontinuation = []

        frame = HandlerFrame(handler=dummy_handler, saved_env=env)
        result = frame.on_value(42, current_env, store, k)

        assert isinstance(result, ContinueValue)
        assert result.value == 42
        assert result.env == env

    def test_handler_frame_on_error_passes_through(self) -> None:
        def dummy_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            return Program.pure(ContinueValue(value=None, env=ctx.env, store=ctx.store, k=[]))

        env: Environment = FrozenDict({"original": "env"})
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test error")

        frame = HandlerFrame(handler=dummy_handler, saved_env=env)
        result = frame.on_error(error, FrozenDict(), store, k)

        assert isinstance(result, ContinueError)
        assert result.error is error
        assert result.env == env


class TestHandlerResultFrame:
    def test_on_value_with_continue_value(self) -> None:
        original_effect = DummyEffect(value=1)
        handled_k: Kontinuation = []

        frame = HandlerResultFrame(
            original_effect=original_effect,
            handler_depth=0,
            handled_program_k=handled_k,
        )

        handler_result = ContinueValue(
            value=100, env=FrozenDict(), store={"new": "store"}, k=[]
        )

        result = frame.on_value(handler_result, FrozenDict(), {}, [])

        assert isinstance(result, ContinueValue)
        assert result.value == 100
        assert result.k == handled_k

    def test_on_value_with_continue_error(self) -> None:
        original_effect = DummyEffect(value=1)
        handled_k: Kontinuation = []
        test_error = RuntimeError("handler decided to fail")

        frame = HandlerResultFrame(
            original_effect=original_effect,
            handler_depth=0,
            handled_program_k=handled_k,
        )

        handler_result = ContinueError(
            error=test_error, env=FrozenDict(), store={}, k=[]
        )

        result = frame.on_value(handler_result, FrozenDict(), {}, [])

        assert isinstance(result, ContinueError)
        assert result.error is test_error
        assert result.k == handled_k

    def test_on_value_with_resume_k(self) -> None:
        original_effect = DummyEffect(value=1)
        handled_k: Kontinuation = []
        new_k: Kontinuation = []

        frame = HandlerResultFrame(
            original_effect=original_effect,
            handler_depth=0,
            handled_program_k=handled_k,
        )

        handler_result = ResumeK(k=new_k, value="switched")

        result = frame.on_value(handler_result, FrozenDict(), {}, [])

        assert isinstance(result, ContinueValue)
        assert result.value == "switched"
        assert result.k == new_k

    def test_on_error_propagates_handler_failure(self) -> None:
        original_effect = DummyEffect(value=1)
        handled_k: Kontinuation = []
        handler_error = RuntimeError("handler crashed")

        frame = HandlerResultFrame(
            original_effect=original_effect,
            handler_depth=0,
            handled_program_k=handled_k,
        )

        result = frame.on_error(handler_error, FrozenDict(), {}, [])

        assert isinstance(result, ContinueError)
        assert result.error is handler_error


class TestWithHandlerEffect:
    def test_with_handler_is_effect(self) -> None:
        def handler(effect: EffectBase, ctx: HandlerContext) -> Program[FrameResult]:
            return Program.pure(ContinueValue(value=None, env=ctx.env, store=ctx.store, k=[]))

        program = Program.pure(42)
        effect = WithHandler(handler=handler, program=program)

        assert isinstance(effect, EffectBase)
        assert effect.handler is handler
        assert effect.program is program


class TestWithHandlerIntegration:
    def test_with_handler_creates_handler_frame_in_k(self) -> None:
        from doeff.cesk import SyncRuntime

        handled_effects: list[EffectBase] = []

        def test_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            handled_effects.append(effect)
            return Program.pure(
                ContinueValue(
                    value=f"handled: {effect}",
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            )

        @do
        def inner_program() -> Program[str]:
            result = yield DummyEffect(value=42)
            return result

        @do
        def outer_program() -> Program[str]:
            result = yield WithHandler(handler=test_handler, program=inner_program())
            return result

        runtime = SyncRuntime()
        result = runtime.run(outer_program())

        assert result.is_ok()
        assert len(handled_effects) == 1
        assert isinstance(handled_effects[0], DummyEffect)

    def test_handler_returns_continue_value_resumes_program(self) -> None:
        from doeff.cesk import SyncRuntime

        def value_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            if isinstance(effect, DummyEffect):
                return Program.pure(
                    ContinueValue(
                        value=effect.value * 2,
                        env=ctx.env,
                        store=ctx.store,
                        k=ctx.delimited_k,
                    )
                )
            return Program.pure(
                ContinueValue(
                    value=None, env=ctx.env, store=ctx.store, k=ctx.delimited_k
                )
            )

        @do
        def test_program() -> Program[int]:
            x = yield DummyEffect(value=21)
            return x

        @do
        def with_handler_program() -> Program[int]:
            return (yield WithHandler(handler=value_handler, program=test_program()))

        runtime = SyncRuntime()
        result = runtime.run(with_handler_program())

        assert result.is_ok()
        assert result.value == 42

    def test_handler_yields_effect_bubbles_to_outer(self) -> None:
        from doeff.cesk import SyncRuntime

        outer_handled: list[EffectBase] = []

        def outer_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            outer_handled.append(effect)
            return Program.pure(
                ContinueValue(
                    value="outer handled",
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            )

        @do
        def inner_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            if isinstance(effect, DummyEffect):
                result = yield AnotherDummyEffect(message="from inner")
                return ContinueValue(
                    value=result,
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            return ContinueValue(
                value=None, env=ctx.env, store=ctx.store, k=ctx.delimited_k
            )

        @do
        def innermost() -> Program[str]:
            return (yield DummyEffect(value=1))

        @do
        def nested_handlers() -> Program[str]:
            inner_result = yield WithHandler(handler=inner_handler, program=innermost())
            return inner_result

        @do
        def full_program() -> Program[str]:
            return (yield WithHandler(handler=outer_handler, program=nested_handlers()))

        runtime = SyncRuntime()
        result = runtime.run(full_program())

        assert result.is_ok()
        assert len(outer_handled) == 1
        assert isinstance(outer_handled[0], AnotherDummyEffect)

    def test_nested_handlers_innermost_first(self) -> None:
        from doeff.cesk import SyncRuntime

        handler_order: list[str] = []

        def make_handler(name: str) -> Handler:
            def handler(
                effect: EffectBase, ctx: HandlerContext
            ) -> Program[FrameResult]:
                handler_order.append(name)
                return Program.pure(
                    ContinueValue(
                        value=f"handled by {name}",
                        env=ctx.env,
                        store=ctx.store,
                        k=ctx.delimited_k,
                    )
                )
            return handler

        @do
        def inner() -> Program[str]:
            return (yield DummyEffect(value=1))

        @do
        def with_inner() -> Program[str]:
            return (yield WithHandler(handler=make_handler("inner"), program=inner()))

        @do
        def with_both() -> Program[str]:
            return (yield WithHandler(handler=make_handler("outer"), program=with_inner()))

        runtime = SyncRuntime()
        result = runtime.run(with_both())

        assert result.is_ok()
        assert handler_order == ["inner"]

    def test_handler_forward_skips_self(self) -> None:
        from doeff.cesk import SyncRuntime

        handler_invocations: list[str] = []

        @do
        def forwarding_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            handler_invocations.append("forwarding")
            result = yield effect
            return ContinueValue(
                value=result, env=ctx.env, store=ctx.store, k=ctx.delimited_k
            )

        def final_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            handler_invocations.append("final")
            return Program.pure(
                ContinueValue(
                    value="final value",
                    env=ctx.env,
                    store=ctx.store,
                    k=ctx.delimited_k,
                )
            )

        @do
        def inner() -> Program[str]:
            return (yield DummyEffect(value=1))

        @do
        def with_forwarding() -> Program[str]:
            return (yield WithHandler(handler=forwarding_handler, program=inner()))

        @do
        def full() -> Program[str]:
            return (yield WithHandler(handler=final_handler, program=with_forwarding()))

        runtime = SyncRuntime()
        result = runtime.run(full())

        assert result.is_ok()
        assert handler_invocations == ["forwarding", "final"]

    def test_resume_k_switches_continuation(self) -> None:
        from doeff.cesk import SyncRuntime

        def switch_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            return Program.pure(ResumeK(k=[], value="switched early"))

        @do
        def program_with_more_work() -> Program[str]:
            yield DummyEffect(value=1)
            return "should not reach here"

        @do
        def main() -> Program[str]:
            return (yield WithHandler(handler=switch_handler, program=program_with_more_work()))

        runtime = SyncRuntime()
        result = runtime.run(main())

        assert result.is_ok()
        assert result.value == "switched early"

    def test_outermost_handler_uses_primitives(self) -> None:
        from doeff.cesk import SyncRuntime

        def store_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            if isinstance(effect, DummyEffect):
                current = ctx.store.get("counter", 0)
                ctx.store["counter"] = current + effect.value
                return Program.pure(
                    ContinueValue(
                        value=ctx.store["counter"],
                        env=ctx.env,
                        store=ctx.store,
                        k=ctx.delimited_k,
                    )
                )
            return Program.pure(
                ContinueValue(
                    value=None, env=ctx.env, store=ctx.store, k=ctx.delimited_k
                )
            )

        @do
        def increment_program() -> Program[int]:
            a = yield DummyEffect(value=10)
            b = yield DummyEffect(value=5)
            return a + b

        @do
        def main() -> Program[int]:
            return (yield WithHandler(handler=store_handler, program=increment_program()))

        runtime = SyncRuntime()
        result = runtime.run(main(), store={"counter": 0})

        assert result.is_ok()
        assert result.value == 25

    def test_unhandled_effect_raises_error(self) -> None:
        from doeff.cesk import SyncRuntime
        from doeff.cesk.errors import UnhandledEffectError

        def noop_handler(
            effect: EffectBase, ctx: HandlerContext
        ) -> Program[FrameResult]:
            return Program.pure(
                ContinueValue(
                    value=None, env=ctx.env, store=ctx.store, k=ctx.delimited_k
                )
            )

        @do
        def program_with_unhandled() -> Program[str]:
            yield DummyEffect(value=1)
            return "ok"

        runtime = SyncRuntime()

        with pytest.raises(UnhandledEffectError):
            runtime.run(program_with_unhandled())
