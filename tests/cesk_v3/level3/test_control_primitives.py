from dataclasses import dataclass

from doeff.cesk_v3 import (
    EffectBase,
    Forward,
    Resume,
    WithHandler,
    run,
)
from doeff.do import do


@dataclass(frozen=True)
class SampleEffect(EffectBase):
    value: int


@dataclass(frozen=True)
class OtherEffect(EffectBase):
    name: str


class TestImplicitAbandonment:

    def test_handler_return_abandons_user_continuation(self) -> None:
        user_continued = []

        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                return "abandoned"
            return (yield Forward(effect))

        @do
        def user():
            result = yield SampleEffect(1)
            user_continued.append(f"continued with {result}")
            return result

        result = run(WithHandler(handler, user()))

        assert result == "abandoned"
        assert user_continued == []

    def test_implicit_abandonment_skips_remaining_computation(self) -> None:
        execution_trace = []

        @do
        def handler(effect):
            execution_trace.append("handler: got effect")
            return "early_exit"

        @do
        def user():
            execution_trace.append("user: before yield")
            result = yield SampleEffect(1)
            execution_trace.append(f"user: after yield with {result}")
            return result * 2

        result = run(WithHandler(handler, user()))

        assert result == "early_exit"
        assert execution_trace == [
            "user: before yield",
            "handler: got effect",
        ]

    def test_nested_handlers_abandonment_skips_inner(self) -> None:
        execution_trace = []

        @do
        def outer_handler(effect):
            execution_trace.append(f"outer: got {effect}")
            if isinstance(effect, SampleEffect):
                return "outer_abandoned"
            return (yield Forward(effect))

        @do
        def inner_handler(effect):
            execution_trace.append(f"inner: got {effect}")
            return (yield Forward(effect))

        @do
        def user():
            execution_trace.append("user: start")
            result = yield SampleEffect(1)
            execution_trace.append(f"user: got {result}")
            return result

        @do
        def main():
            return (yield WithHandler(inner_handler, user()))

        result = run(WithHandler(outer_handler, main()))

        assert result == "outer_abandoned"
        assert "user: got" not in execution_trace


class TestReYieldForwarding:

    def test_reyield_forwards_to_outer_handler(self) -> None:
        @do
        def outer_handler(effect):
            if isinstance(effect, SampleEffect):
                return (yield Resume(effect.value * 10))
            return (yield Forward(effect))

        @do
        def inner_handler(effect):
            if isinstance(effect, OtherEffect):
                return (yield Resume(effect.name.upper()))
            result = yield effect
            return (yield Resume(result))

        @do
        def user():
            return (yield SampleEffect(5))

        @do
        def main():
            return (yield WithHandler(inner_handler, user()))

        result = run(WithHandler(outer_handler, main()))
        assert result == 50

    def test_forward_then_resume_raises_error(self) -> None:
        from doeff.cesk_v3.run import sync_run

        @do
        def outer_handler(effect):
            if isinstance(effect, SampleEffect):
                return (yield Resume(100))
            return (yield Forward(effect))

        @do
        def inner_handler_forward(effect):
            result = yield Forward(effect)
            return (yield Resume(result))

        @do
        def user():
            return (yield SampleEffect(1))

        @do
        def main_forward():
            return (yield WithHandler(inner_handler_forward, user()))

        result = sync_run(WithHandler(outer_handler, main_forward()))
        assert result.is_error
        assert "Resume called after Forward" in str(result.error)

    def test_reyield_chains_through_multiple_handlers(self) -> None:
        trace = []

        @do
        def handler_a(effect):
            if isinstance(effect, SampleEffect):
                trace.append("A: handling")
                return (yield Resume(effect.value + 1))
            trace.append("A: forwarding")
            result = yield effect
            trace.append(f"A: got {result}")
            return (yield Resume(result))

        @do
        def handler_b(effect):
            trace.append("B: forwarding")
            result = yield effect
            trace.append(f"B: got {result}")
            return (yield Resume(result))

        @do
        def handler_c(effect):
            trace.append("C: forwarding")
            result = yield effect
            trace.append(f"C: got {result}")
            return (yield Resume(result))

        @do
        def user():
            return (yield SampleEffect(10))

        @do
        def with_c():
            return (yield WithHandler(handler_c, user()))

        @do
        def with_b():
            return (yield WithHandler(handler_b, with_c()))

        result = run(WithHandler(handler_a, with_b()))

        assert result == 11
        assert "A: handling" in trace


class TestForwardExplicit:

    def test_forward_without_resume(self) -> None:
        @do
        def outer_handler(effect):
            if isinstance(effect, SampleEffect):
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def inner_handler(effect):
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(20))

        @do
        def main():
            return (yield WithHandler(inner_handler, user()))

        result = run(WithHandler(outer_handler, main()))

        assert result == 40


