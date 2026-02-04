from dataclasses import dataclass

import pytest

from doeff.cesk_v3 import (
    CreateContinuation,
    EffectBase,
    Forward,
    GetContinuation,
    GetHandlers,
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
class Spawn(EffectBase):
    pass


@dataclass(frozen=True)
class Yield(EffectBase):
    pass


class TestGetHandlers:

    def test_get_handlers_returns_handler_tuple(self) -> None:
        captured_handlers = [None]

        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                handlers = yield GetHandlers()
                captured_handlers[0] = handlers
                return (yield Resume(42))
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(1))

        result = run(WithHandler(handler, user()))

        assert result == 42
        assert captured_handlers[0] is not None
        assert isinstance(captured_handlers[0], tuple)
        assert len(captured_handlers[0]) == 1

    def test_get_handlers_returns_yielders_scope_not_handlers(self) -> None:
        outer_captured = [None]

        @do
        def outer_handler(effect):
            if isinstance(effect, SampleEffect):
                outer_captured[0] = yield GetHandlers()
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def inner_handler(effect):
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(21))

        @do
        def main():
            return (yield WithHandler(inner_handler, user()))

        result = run(WithHandler(outer_handler, main()))

        assert result == 42
        assert outer_captured[0] is not None
        assert len(outer_captured[0]) == 2

    def test_get_handlers_outside_dispatch_raises(self) -> None:
        @do
        def bad_program():
            return (yield GetHandlers())

        with pytest.raises(RuntimeError, match="outside handler context"):
            run(bad_program())

    def test_get_handlers_multiple_times_same_result(self) -> None:
        results = []

        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                h1 = yield GetHandlers()
                h2 = yield GetHandlers()
                results.append(h1)
                results.append(h2)
                return (yield Resume(1))
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(0))

        run(WithHandler(handler, user()))

        assert len(results) == 2
        assert results[0] == results[1]


class TestCreateContinuation:

    def test_create_continuation_returns_unstarted(self) -> None:
        created_cont = [None]

        @do
        def handler(effect):
            if isinstance(effect, Spawn):
                @do
                def child():
                    return "child_result"

                cont = yield CreateContinuation(child(), handlers=())
                created_cont[0] = cont
                assert cont.started is False
                assert cont.program is not None
                return (yield Resume("spawned"))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        result = run(WithHandler(handler, user()))

        assert result == "spawned"
        assert created_cont[0] is not None
        assert created_cont[0].started is False

    def test_create_continuation_with_empty_handlers(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, Spawn):
                @do
                def child():
                    return 42

                cont = yield CreateContinuation(child(), handlers=())
                assert len(cont.handlers) == 0
                return (yield Resume("done"))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        result = run(WithHandler(handler, user()))
        assert result == "done"

    def test_create_continuation_with_inherited_handlers(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, Spawn):
                parent_handlers = yield GetHandlers()

                @do
                def child():
                    return 100

                cont = yield CreateContinuation(child(), handlers=parent_handlers)
                assert len(cont.handlers) == len(parent_handlers)
                return (yield Resume("inherited"))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        result = run(WithHandler(handler, user()))
        assert result == "inherited"


class TestResumeContinuationUnstarted:

    def test_resume_unstarted_continuation_executes_program(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, Spawn):
                @do
                def child():
                    return "child_executed"

                cont = yield CreateContinuation(child(), handlers=())
                return (yield ResumeContinuation(cont, None))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        result = run(WithHandler(handler, user()))
        assert result == "child_executed"

    def test_resume_unstarted_with_handlers_sees_handlers(self) -> None:
        trace = []

        @do
        def logging_handler(effect):
            if isinstance(effect, SampleEffect):
                trace.append(f"logged: {effect.value}")
                return (yield Resume(effect.value * 2))
            return (yield Forward(effect))

        @do
        def spawn_handler(effect):
            if isinstance(effect, Spawn):
                handlers = yield GetHandlers()

                @do
                def child():
                    x = yield SampleEffect(21)
                    return x

                cont = yield CreateContinuation(child(), handlers=handlers)
                return (yield ResumeContinuation(cont, None))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        @do
        def main():
            return (yield WithHandler(spawn_handler, user()))

        result = run(WithHandler(logging_handler, main()))

        assert result == 42
        assert trace == ["logged: 21"]

    def test_unstarted_continuation_one_shot(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, Spawn):
                @do
                def child():
                    return "child"

                cont = yield CreateContinuation(child(), handlers=())
                yield ResumeContinuation(cont, None)
                return (yield ResumeContinuation(cont, None))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        with pytest.raises(RuntimeError, match="already consumed"):
            run(WithHandler(handler, user()))


class TestSpawnPattern:

    def test_spawn_with_interleaved_yield(self) -> None:
        trace = []
        ready_queue: list = []

        @do
        def scheduler_handler(effect):
            if isinstance(effect, Yield):
                trace.append("scheduler: yield")
                k = yield GetContinuation()
                ready_queue.append(k)
                if ready_queue:
                    next_k = ready_queue.pop(0)
                    return (yield ResumeContinuation(next_k, None))
                return (yield Resume(None))

            if isinstance(effect, Spawn):
                trace.append("scheduler: spawn")
                handlers = yield GetHandlers()

                @do
                def child_task():
                    trace.append("child: step 1")
                    yield Yield()
                    trace.append("child: step 2")
                    return "child_done"

                child_k = yield CreateContinuation(child_task(), handlers=handlers)
                parent_k = yield GetContinuation()

                ready_queue.append(parent_k)
                return (yield ResumeContinuation(child_k, None))

            return (yield Forward(effect))

        @do
        def main_task():
            trace.append("main: before spawn")
            result = yield Spawn()
            trace.append(f"main: after spawn, got {result}")
            yield Yield()
            trace.append("main: final step")
            return "main_done"

        result = run(WithHandler(scheduler_handler, main_task()))

        assert "main: before spawn" in trace
        assert "child: step 1" in trace
        assert "child: step 2" in trace


class TestContinuationKinds:

    def test_captured_vs_created_distinction(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                if effect.value == 1:
                    captured = yield GetContinuation()
                    assert captured.started is True
                    assert captured.program is None
                    return (yield Resume("captured"))
                elif effect.value == 2:
                    @do
                    def fresh():
                        return "fresh"

                    created = yield CreateContinuation(fresh(), handlers=())
                    assert created.started is False
                    assert created.program is not None
                    return (yield Resume("created"))
            return (yield Forward(effect))

        @do
        def user():
            r1 = yield SampleEffect(1)
            r2 = yield SampleEffect(2)
            return (r1, r2)

        result = run(WithHandler(handler, user()))
        assert result == ("captured", "created")

    def test_continuation_frames_for_captured(self) -> None:
        @do
        def handler(effect):
            if isinstance(effect, SampleEffect):
                captured = yield GetContinuation()
                from doeff.cesk_v3.level1_cesk.frames import ReturnFrame

                has_return_frame = any(
                    isinstance(f, ReturnFrame) for f in captured.frames
                )
                assert has_return_frame
                return (yield Resume(len(captured.frames)))
            return (yield Forward(effect))

        @do
        def user():
            return (yield SampleEffect(1))

        result = run(WithHandler(handler, user()))
        assert result >= 1

    def test_continuation_handlers_for_created(self) -> None:
        @do
        def some_handler(effect):
            return (yield Forward(effect))

        @do
        def spawn_handler(effect):
            if isinstance(effect, Spawn):
                parent_handlers = yield GetHandlers()

                @do
                def child():
                    return 1

                created = yield CreateContinuation(child(), handlers=parent_handlers)
                assert len(created.handlers) == len(parent_handlers)
                return (yield Resume("checked"))
            return (yield Forward(effect))

        @do
        def user():
            return (yield Spawn())

        @do
        def main():
            return (yield WithHandler(spawn_handler, user()))

        result = run(WithHandler(some_handler, main()))
        assert result == "checked"
