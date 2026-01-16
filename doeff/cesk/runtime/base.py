"""Base runtime for CESK machine execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doeff.cesk.handlers import Handler
    from doeff.cesk.state import CESKState
    from doeff.program import Program


class BaseRuntime(ABC):

    def __init__(self, handlers: dict[type, Handler] | None = None):
        if handlers is None:
            from doeff.cesk.handlers import default_handlers

            handlers = default_handlers()
        self._handlers = handlers

    @abstractmethod
    def run(
        self,
        program: Program,
        env: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
    ) -> Any:
        ...

    def step_until_done(self, state: CESKState) -> Any:
        from doeff.cesk.frames import (
            ContinueError,
            ContinueGenerator,
            ContinueProgram,
            ContinueValue,
        )
        from doeff.cesk.state import (
            Done as TaskDone,
            EffectControl,
            Error as ErrorControl,
            ProgramControl,
            TaskState,
            Value as ValueControl,
        )
        from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
        from doeff.program import KleisliProgramCall, ProgramBase
        from doeff._types_internal import EffectBase

        MAX_STEPS = 1_000_000

        for step_count in range(MAX_STEPS):
            main_task = state.tasks[state.main_task]

            if isinstance(main_task.status, TaskDone):
                result = main_task.status.result
                if result.is_ok():
                    return result.value
                else:
                    raise result.error

            control = main_task.control
            env = main_task.env
            k = main_task.kontinuation
            store = state.store

            if isinstance(control, ValueControl) and not k:
                new_task = main_task.with_status(TaskDone.ok(control.v))
                state = state.with_task(state.main_task, new_task)
                continue

            if isinstance(control, ErrorControl) and not k:
                error_to_raise = control.ex
                if control.captured_traceback:
                    error_to_raise = control.ex
                new_task = main_task.with_status(TaskDone.err(error_to_raise))
                state = state.with_task(state.main_task, new_task)
                continue

            if isinstance(control, EffectControl):
                effect = control.effect
                effect_type = type(effect)

                if effect_type not in self._handlers:
                    error = ValueError(f"No handler for effect type: {effect_type.__name__}")
                    new_task = main_task.with_control(ErrorControl(error))
                    state = state.with_task(state.main_task, new_task)
                    continue

                handler = self._handlers[effect_type]
                frame_result = handler(effect, main_task, store)

                if isinstance(frame_result, ContinueValue):
                    new_task = TaskState(
                        control=ValueControl(frame_result.value),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueError):
                    new_task = TaskState(
                        control=ErrorControl(frame_result.error, frame_result.captured_traceback),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueProgram):
                    new_task = TaskState(
                        control=ProgramControl(frame_result.program),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueGenerator):
                    new_task = TaskState(
                        control=ProgramControl(frame_result.program) if hasattr(frame_result, 'program') and frame_result.program else ValueControl(None),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

            if isinstance(control, ProgramControl):
                from doeff.cesk.helpers import to_generator

                program = control.program
                pre_captured = None

                try:
                    gen = to_generator(program)
                    program_call = program if isinstance(program, KleisliProgramCall) else None
                    pre_captured = pre_capture_generator(gen, is_resumed=False, program_call=program_call)
                    item = next(gen)

                    if isinstance(item, EffectBase):
                        new_control = EffectControl(item)
                    elif isinstance(item, ProgramBase):
                        new_control = ProgramControl(item)
                    else:
                        error = TypeError(f"Program yielded unexpected type: {type(item).__name__}")
                        new_task = main_task.with_control(ErrorControl(error))
                        state = state.with_task(state.main_task, new_task)
                        continue

                    from doeff.cesk.frames import ReturnFrame

                    new_task = TaskState(
                        control=new_control,
                        env=env,
                        kontinuation=[ReturnFrame(gen, env, program_call=program_call)] + k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                except StopIteration as e:
                    new_task = main_task.with_control(ValueControl(e.value))
                    state = state.with_task(state.main_task, new_task)
                    continue

                except Exception as ex:
                    captured = capture_traceback_safe(k, ex, pre_captured=pre_captured)
                    new_task = main_task.with_control(ErrorControl(ex, captured_traceback=captured))
                    state = state.with_task(state.main_task, new_task)
                    continue

            if isinstance(control, ValueControl) and k:
                frame = k[0]
                k_rest = k[1:]
                frame_result = frame.on_value(control.v, env, store, k_rest)

                if isinstance(frame_result, ContinueValue):
                    new_task = TaskState(
                        control=ValueControl(frame_result.value),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueError):
                    new_task = TaskState(
                        control=ErrorControl(frame_result.error, frame_result.captured_traceback),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueProgram):
                    new_task = TaskState(
                        control=ProgramControl(frame_result.program),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueGenerator):
                    pre_captured = pre_capture_generator(
                        frame_result.generator,
                        is_resumed=True,
                        program_call=frame_result.program_call,
                    )

                    try:
                        if frame_result.throw_error:
                            item = frame_result.generator.throw(frame_result.throw_error)
                        else:
                            item = frame_result.generator.send(frame_result.send_value)

                        if isinstance(item, EffectBase):
                            new_control = EffectControl(item)
                        elif isinstance(item, ProgramBase):
                            new_control = ProgramControl(item)
                        else:
                            error = TypeError(f"Generator yielded unexpected type: {type(item).__name__}")
                            new_task = main_task.with_control(ErrorControl(error))
                            state = state.with_task(state.main_task, new_task)
                            continue

                        from doeff.cesk.frames import ReturnFrame

                        new_task = TaskState(
                            control=new_control,
                            env=frame_result.env,
                            kontinuation=[ReturnFrame(frame_result.generator, frame_result.env, program_call=frame_result.program_call)] + frame_result.k,
                            status=main_task.status,
                        )
                        state = state.with_task(state.main_task, new_task)
                        continue

                    except StopIteration as e:
                        new_task = TaskState(
                            control=ValueControl(e.value),
                            env=frame_result.env,
                            kontinuation=frame_result.k,
                            status=main_task.status,
                        )
                        state = state.with_task(state.main_task, new_task)
                        continue

                    except Exception as ex:
                        captured = capture_traceback_safe(frame_result.k, ex, pre_captured=pre_captured)
                        new_task = TaskState(
                            control=ErrorControl(ex, captured_traceback=captured),
                            env=frame_result.env,
                            kontinuation=frame_result.k,
                            status=main_task.status,
                        )
                        state = state.with_task(state.main_task, new_task)
                        continue

            if isinstance(control, ErrorControl) and k:
                frame = k[0]
                k_rest = k[1:]
                frame_result = frame.on_error(control.ex, env, store, k_rest)

                if isinstance(frame_result, ContinueValue):
                    new_task = TaskState(
                        control=ValueControl(frame_result.value),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueError):
                    new_task = TaskState(
                        control=ErrorControl(frame_result.error, frame_result.captured_traceback),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueProgram):
                    new_task = TaskState(
                        control=ProgramControl(frame_result.program),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

                elif isinstance(frame_result, ContinueGenerator):
                    new_task = TaskState(
                        control=ErrorControl(control.ex),
                        env=frame_result.env,
                        kontinuation=frame_result.k,
                        status=main_task.status,
                    )
                    state = state.with_task(state.main_task, new_task)
                    continue

            error = RuntimeError(f"Unexpected CESK state: {control}")
            new_task = main_task.with_control(ErrorControl(error))
            state = state.with_task(state.main_task, new_task)

        raise RuntimeError(f"Exceeded maximum steps ({MAX_STEPS})")


__all__ = ["BaseRuntime"]
