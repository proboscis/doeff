from dataclasses import dataclass

import pytest

from doeff.cesk_v3 import (
    EffectBase,
    Forward,
    GetContinuation,
    Resume,
    ResumeContinuation,
    WithHandler,
    run,
)
from doeff.do import do


@dataclass(frozen=True)
class SampleEffect(EffectBase):
    value: int


@dataclass(frozen=True)
class Yield(EffectBase):
    pass


class TestGetContinuationBasic:

    def test_get_continuation_returns_continuation_object(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                k = yield GetContinuation()
                assert k is not None
                assert k.cont_id > 0
                assert len(k.frames) >= 0
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def user():
            result = yield SampleEffect(21)
            return result

        result = run(WithHandler(handler, user()))
        assert result == 42

    def test_get_continuation_does_not_consume_df(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                k1 = yield GetContinuation()
                k2 = yield GetContinuation()
                assert k1.frames == k2.frames
                return (yield Resume(100))
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(1))

        result = run(WithHandler(handler, user()))
        assert result == 100

    def test_get_continuation_can_still_resume_after(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                k = yield GetContinuation()
                _ = k
                return (yield Resume(effect.value + 1))
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(41))

        result = run(WithHandler(handler, user()))
        assert result == 42


class TestResumeContinuationBasic:

    def test_resume_captured_continuation_immediately(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                k = yield GetContinuation()
                return (yield ResumeContinuation(k, effect.value * 2))
            return (yield Forward(effect))

        @do
        def user():
            result = yield SampleEffect(21)
            return result + 1

        result = run(WithHandler(handler, user()))
        assert result == 43

    def test_one_shot_violation_raises_error(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                k = yield GetContinuation()
                yield ResumeContinuation(k, 1)
                return (yield ResumeContinuation(k, 2))
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(1))

        with pytest.raises(RuntimeError, match="already consumed"):
            run(WithHandler(handler, user()))


class TestSchedulingPattern:

    def test_simple_yield_and_resume(self) -> None:
        execution_order: list[str] = []

        @do
        def scheduler_handler(effect):
            if isinstance(effect, Yield):
                execution_order.append("scheduler: yield received")
                k = yield GetContinuation()
                execution_order.append(f"scheduler: captured k with {len(k.frames)} frames")
                return (yield ResumeContinuation(k, "resumed"))
            return (yield Forward(effect))

        @do
        def user():
            execution_order.append("user: before yield")
            result = yield Yield()
            execution_order.append(f"user: after yield, got {result}")
            return result

        result = run(WithHandler(scheduler_handler, user()))

        assert result == "resumed"
        assert execution_order == [
            "user: before yield",
            "scheduler: yield received",
            "scheduler: captured k with 1 frames",
            "user: after yield, got resumed",
        ]

    def test_multiple_yields(self) -> None:
        yield_count = [0]

        @do
        def scheduler_handler(effect):
            if isinstance(effect, Yield):
                yield_count[0] += 1
                k = yield GetContinuation()
                return (yield ResumeContinuation(k, yield_count[0]))
            return (yield Forward(effect))

        @do
        def user():
            r1 = yield Yield()
            r2 = yield Yield()
            r3 = yield Yield()
            return (r1, r2, r3)

        result = run(WithHandler(scheduler_handler, user()))
        assert result == (1, 2, 3)


class TestContinuationWithNestedHandlers:

    def test_get_continuation_with_nested_handler(self) -> None:
        @do
        def outer_handler(effect):
            if isinstance(effect, SampleEffect):
                k = yield GetContinuation()
                return (yield ResumeContinuation(k, effect.value + 100))
            forward_result = yield Forward(effect)
            return (yield Resume(forward_result))

        @dataclass(frozen=True)
        class InnerEffect(EffectBase):
            pass

        @do
        def inner_handler(effect):
            if isinstance(effect, InnerEffect):
                return (yield Resume("inner"))
            return (yield Forward(effect))

        @do
        def user():
            inner_result = yield InnerEffect()
            outer_result = yield SampleEffect(1)
            return (inner_result, outer_result)

        @do
        def main():
            return (yield WithHandler(inner_handler, user()))

        result = run(WithHandler(outer_handler, main()))
        assert result == ("inner", 101)


class TestResumeContinuationPreservesHandlerGen:

    def test_handler_receives_final_result(self) -> None:
        results: list[str] = []

        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                k = yield GetContinuation()
                user_result = yield ResumeContinuation(k, 10)
                results.append(f"handler received: {user_result}")
                return user_result + 1
            return (yield Forward(effect))

        @do
        def user():
            x = yield SampleEffect(0)
            return x * 2

        result = run(WithHandler(handler, user()))

        assert result == 21
        assert results == ["handler received: 20"]


class TestMultiTaskInterleaving:

    def test_cooperative_scheduler_with_queue(self) -> None:
        execution_trace: list[str] = []
        task_queue: list[tuple[str, object]] = []

        @do
        def scheduler_handler(effect):
            if isinstance(effect, Yield):
                k = yield GetContinuation()
                task_queue.append(("current", k))

                if task_queue:
                    task_name, next_k = task_queue.pop(0)
                    execution_trace.append(f"scheduler: switching to {task_name}")
                    return (yield ResumeContinuation(next_k, None))

                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def task_a():
            execution_trace.append("A: step 1")
            yield Yield()
            execution_trace.append("A: step 2")
            yield Yield()
            execution_trace.append("A: step 3")
            return "A_done"

        result = run(WithHandler(scheduler_handler, task_a()))

        assert result == "A_done"
        assert execution_trace == [
            "A: step 1",
            "scheduler: switching to current",
            "A: step 2",
            "scheduler: switching to current",
            "A: step 3",
        ]

    def test_two_tasks_interleaved(self) -> None:
        execution_trace: list[str] = []

        @do
        def task_a():
            execution_trace.append("A1")
            yield Yield()
            execution_trace.append("A2")
            yield Yield()
            execution_trace.append("A3")
            return "A"

        @do
        def task_b():
            execution_trace.append("B1")
            yield Yield()
            execution_trace.append("B2")
            return "B"

        task_queue: list[object] = []
        task_a_continuation = [None]
        task_b_continuation = [None]

        @do
        def interleaving_scheduler(effect):
            if isinstance(effect, Yield):
                k = yield GetContinuation()

                if task_b_continuation[0] is None:
                    task_a_continuation[0] = k
                    task_b_gen = task_b()
                    task_b_continuation[0] = "pending"
                    return (yield WithHandler(interleaving_scheduler, task_b_gen))

                if task_a_continuation[0] is not None:
                    saved_a = task_a_continuation[0]
                    task_a_continuation[0] = None
                    task_queue.append(k)
                    return (yield ResumeContinuation(saved_a, None))

                if task_queue:
                    next_k = task_queue.pop(0)
                    task_queue.append(k)
                    return (yield ResumeContinuation(next_k, None))

                return (yield Resume(None))
            return (yield Forward(effect))

        result = run(WithHandler(interleaving_scheduler, task_a()))

        assert "A1" in execution_trace
        assert "B1" in execution_trace
        assert execution_trace.index("A1") < execution_trace.index("B1")

    def test_round_robin_scheduler(self) -> None:
        execution_trace: list[str] = []
        ready_queue: list[object] = []

        @do
        def round_robin_handler(effect):
            if isinstance(effect, Yield):
                k = yield GetContinuation()
                ready_queue.append(k)

                if ready_queue:
                    next_k = ready_queue.pop(0)
                    return (yield ResumeContinuation(next_k, None))
                return (yield Resume(None))
            return (yield Forward(effect))

        @do
        def worker(name: str, steps: int):
            for i in range(steps):
                execution_trace.append(f"{name}:{i}")
                yield Yield()
            return name

        result = run(WithHandler(round_robin_handler, worker("W", 3)))

        assert result == "W"
        assert execution_trace == ["W:0", "W:1", "W:2"]

    def test_context_switch_abandons_current_computation(self) -> None:
        trace: list[str] = []
        captured_k = [None]

        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                if effect.value == 1:
                    trace.append("handler: capturing k for effect 1")
                    captured_k[0] = yield GetContinuation()
                    trace.append("handler: resuming effect 1 normally")
                    return (yield Resume("first"))
                elif effect.value == 2:
                    trace.append("handler: got effect 2, switching to captured k")
                    return (yield ResumeContinuation(captured_k[0], "switched"))
            return (yield Forward(effect))

        @do
        def user():
            trace.append("user: before effect 1")
            r1 = yield SampleEffect(1)
            trace.append(f"user: after effect 1, got {r1}")
            r2 = yield SampleEffect(2)
            trace.append(f"user: after effect 2, got {r2}")
            return (r1, r2)

        result = run(WithHandler(handler, user()))

        # ResumeContinuation with captured_k sends "switched" to effect 2's yield point
        # (the continuation was captured at effect 1, but after Resume("first") the program
        # continued to effect 2, which is where ResumeContinuation injects "switched")
        assert result == ("first", "switched")
        assert "user: after effect 1, got first" in trace
        assert "user: after effect 2, got switched" in trace
