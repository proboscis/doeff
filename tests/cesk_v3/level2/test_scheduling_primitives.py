from __future__ import annotations

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    Continuation,
    CreateContinuation,
    Forward,
    GetContinuation,
    GetHandlers,
    Resume,
    ResumeContinuation,
    WithHandler,
)
from doeff.cesk_v3.level3_core_effects import Get, Put, state_handler
from doeff.cesk_v3.run import sync_run
from doeff.do import do
from doeff.program import Program


class TestGetContinuation:
    def test_captures_continuation_and_can_resume_immediately(self):
        captured_cont: list[Continuation] = []

        @do
        def capture_handler(effect: EffectBase) -> Program:
            if isinstance(effect, YieldEffect):
                k = yield GetContinuation()
                captured_cont.append(k)
                return (yield Resume("resumed"))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[str]:
            result = yield YieldEffect()
            return f"got: {result}"

        result = sync_run(WithHandler(capture_handler, program()))
        assert result.unwrap() == "got: resumed"
        assert len(captured_cont) == 1
        assert isinstance(captured_cont[0], Continuation)
        assert captured_cont[0].started is True

    def test_captured_continuation_has_frames(self):
        captured_cont: list[Continuation] = []

        @do
        def capture_handler(effect: EffectBase) -> Program:
            if isinstance(effect, YieldEffect):
                k = yield GetContinuation()
                captured_cont.append(k)
                return (yield Resume(None))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def inner() -> Program[None]:
            yield YieldEffect()
            return None

        @do
        def program() -> Program[None]:
            yield inner()
            return None

        sync_run(WithHandler(capture_handler, program()))
        assert len(captured_cont) == 1
        assert len(captured_cont[0].frames) > 0


class TestResumeContinuation:
    def test_resume_captured_continuation_with_value(self):
        saved_cont: list[Continuation] = []
        execution_order: list[str] = []

        @do
        def scheduler_handler(effect: EffectBase) -> Program:
            if isinstance(effect, YieldEffect):
                execution_order.append("handler_got_yield")
                k = yield GetContinuation()
                saved_cont.append(k)
                return (yield Resume("first_resume"))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[str]:
            execution_order.append("before_yield")
            result = yield YieldEffect()
            execution_order.append(f"after_yield:{result}")
            return result

        result = sync_run(WithHandler(scheduler_handler, program()))
        assert result.unwrap() == "first_resume"
        assert execution_order == ["before_yield", "handler_got_yield", "after_yield:first_resume"]

    def test_resume_different_continuation_switches_context(self):
        task_queue: list[tuple[str, Continuation]] = []
        execution_log: list[str] = []

        @do
        def scheduler_handler(effect: EffectBase) -> Program:
            if isinstance(effect, SpawnEffect):
                parent_handlers = yield GetHandlers()
                child_k = yield CreateContinuation(effect.program, parent_handlers)
                parent_k = yield GetContinuation()
                task_queue.append(("parent", parent_k))
                task_queue.append(("child", child_k))
                _, next_k = task_queue.pop(0)
                return (yield ResumeContinuation(next_k, None))
            if isinstance(effect, YieldEffect):
                current_k = yield GetContinuation()
                task_queue.append(("task", current_k))
                if task_queue:
                    _, next_k = task_queue.pop(0)
                    return (yield ResumeContinuation(next_k, None))
                return (yield Resume(None))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def child_task() -> Program[None]:
            execution_log.append("child_start")
            yield YieldEffect()
            execution_log.append("child_end")
            return None

        @do
        def program() -> Program[None]:
            execution_log.append("parent_start")
            yield SpawnEffect(child_task())
            execution_log.append("parent_after_spawn")
            yield YieldEffect()
            execution_log.append("parent_end")
            return None

        sync_run(WithHandler(scheduler_handler, program()))
        assert "parent_start" in execution_log
        assert "child_start" in execution_log

    def test_one_shot_violation_raises_error(self):
        resume_count = [0]

        @do
        def capture_and_resume_twice_handler(effect: EffectBase) -> Program:
            if isinstance(effect, TryDoubleResumeEffect):
                k = yield GetContinuation()
                resume_count[0] += 1
                yield ResumeContinuation(k, "first")
                resume_count[0] += 1
                yield ResumeContinuation(k, "second")
                return (yield Resume("should_not_reach"))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[str]:
            return (yield TryDoubleResumeEffect())

        result = sync_run(WithHandler(capture_and_resume_twice_handler, program()))
        assert result.is_error
        assert "consumed" in str(result.error).lower() or "one-shot" in str(result.error).lower()


class TestGetHandlers:
    def test_returns_handlers_from_dispatch_context(self):
        captured_handlers: list[tuple] = []

        @do
        def my_handler(effect: EffectBase) -> Program:
            if isinstance(effect, GetHandlersEffect):
                handlers = yield GetHandlers()
                captured_handlers.append(handlers)
                return (yield Resume(len(handlers)))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[int]:
            return (yield GetHandlersEffect())

        result = sync_run(WithHandler(my_handler, program()))
        assert result.unwrap() >= 1
        assert len(captured_handlers) == 1
        assert len(captured_handlers[0]) >= 1

    def test_handlers_available_to_user_code(self):
        @do
        def handler_a(effect: EffectBase) -> Program:
            if isinstance(effect, CheckHandlersEffect):
                handlers = yield GetHandlers()
                return (yield Resume(("a_saw", len(handlers))))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def handler_b(effect: EffectBase) -> Program:
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def program() -> Program[tuple]:
            return (yield CheckHandlersEffect())

        result = sync_run(
            WithHandler(handler_b, WithHandler(handler_a, program()))
        )
        label, count = result.unwrap()
        assert label == "a_saw"
        assert count >= 1


class TestCreateContinuation:
    def test_creates_unstarted_continuation(self):
        created_cont: list[Continuation] = []

        @do
        def create_handler(effect: EffectBase) -> Program:
            if isinstance(effect, CreateTaskEffect):
                k = yield CreateContinuation(effect.program, ())
                created_cont.append(k)
                return (yield Resume(k))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def task_program() -> Program[int]:
            return 42

        @do
        def program() -> Program[Continuation]:
            return (yield CreateTaskEffect(task_program()))

        result = sync_run(WithHandler(create_handler, program()))
        k = result.unwrap()
        assert isinstance(k, Continuation)
        assert k.started is False
        assert k.program is not None

    def test_resume_unstarted_continuation_starts_program(self):
        execution_log: list[str] = []

        @do
        def scheduler_handler(effect: EffectBase) -> Program:
            if isinstance(effect, SpawnAndRunEffect):
                handlers = yield GetHandlers()
                child_k = yield CreateContinuation(effect.program, handlers)
                return (yield ResumeContinuation(child_k, None))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def child_program() -> Program[str]:
            execution_log.append("child_executed")
            return "child_result"

        @do
        def program() -> Program[str]:
            execution_log.append("parent_start")
            return (yield SpawnAndRunEffect(child_program()))

        result = sync_run(WithHandler(scheduler_handler, program()))
        assert result.unwrap() == "child_result"
        assert execution_log == ["parent_start", "child_executed"]

    def test_created_continuation_inherits_handlers(self):
        state_accessed: list[bool] = []

        @do
        def spawn_handler(effect: EffectBase) -> Program:
            if isinstance(effect, SpawnAndRunEffect):
                handlers = yield GetHandlers()
                child_k = yield CreateContinuation(effect.program, handlers)
                return (yield ResumeContinuation(child_k, None))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def child_program() -> Program[int]:
            yield Put("child_key", 123)
            value = yield Get("child_key")
            state_accessed.append(True)
            return value

        @do
        def program() -> Program[int]:
            return (yield SpawnAndRunEffect(child_program()))

        result = sync_run(
            WithHandler(state_handler(), WithHandler(spawn_handler, program()))
        )
        assert result.unwrap() == 123
        assert state_accessed == [True]


class TestSchedulingIntegration:
    def test_simple_cooperative_scheduler(self):
        execution_order: list[str] = []
        task_queue: list[Continuation] = []

        @do
        def scheduler_handler(effect: EffectBase) -> Program:
            if isinstance(effect, SpawnEffect):
                handlers = yield GetHandlers()
                child_k = yield CreateContinuation(effect.program, handlers)
                task_queue.append(child_k)
                return (yield Resume(None))
            if isinstance(effect, YieldEffect):
                current_k = yield GetContinuation()
                task_queue.append(current_k)
                if task_queue:
                    next_k = task_queue.pop(0)
                    return (yield ResumeContinuation(next_k, None))
                return (yield Resume(None))
            forwarded = yield Forward(effect)
            return (yield Resume(forwarded))

        @do
        def task_a() -> Program[None]:
            execution_order.append("A1")
            yield YieldEffect()
            execution_order.append("A2")
            return None

        @do
        def task_b() -> Program[None]:
            execution_order.append("B1")
            yield YieldEffect()
            execution_order.append("B2")
            return None

        @do
        def main() -> Program[None]:
            yield SpawnEffect(task_a())
            yield SpawnEffect(task_b())
            execution_order.append("main_done_spawning")
            yield YieldEffect()
            execution_order.append("main_resumed")
            return None

        sync_run(WithHandler(scheduler_handler, main()))
        assert "A1" in execution_order
        assert "B1" in execution_order
        assert "main_done_spawning" in execution_order


@do
def _dummy_handler(effect: EffectBase) -> Program:
    forwarded = yield Forward(effect)
    return (yield Resume(forwarded))


class YieldEffect(EffectBase):
    pass


class SpawnEffect(EffectBase):
    def __init__(self, program: Program):
        self.program = program


class GetHandlersEffect(EffectBase):
    pass


class CreateTaskEffect(EffectBase):
    def __init__(self, program: Program):
        self.program = program


class SpawnAndRunEffect(EffectBase):
    def __init__(self, program: Program):
        self.program = program


class CaptureAndResumeEffect(EffectBase):
    pass


class ResumeAgainEffect(EffectBase):
    pass


class TryDoubleResumeEffect(EffectBase):
    pass


class CheckHandlersEffect(EffectBase):
    pass
