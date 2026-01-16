"""The unified CESK machine step function."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from doeff._vendor import FrozenDict, Ok, Err
from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, Store
from doeff.cesk.frames import (
    Frame,
    GatherFrame,
    InterceptFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.state import (
    CESKState,
    EffectControl,
    ErrorControl,
    ProgramControl,
    ReadyStatus,
    RequestingStatus,
    TaskState,
    ValueControl,
    DoneStatus,
)

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.types import Effect


Handler = Callable[[EffectBase, Kontinuation, Environment, Store], TaskState]


class StepError(Exception):
    pass


class UnhandledEffectError(StepError):
    pass


class InterpreterInvariantError(StepError):
    pass


def _has_real_method(obj: object, name: str) -> bool:
    """Check if obj has a real method, not one synthesized by ProgramBase.__getattr__."""
    for cls in type(obj).__mro__:
        if name in cls.__dict__:
            return True
    return False


def to_generator(program: Program):
    from doeff.program import GeneratorProgram, KleisliProgramCall, ProgramBase
    
    if isinstance(program, GeneratorProgram):
        return program.to_generator()
    if isinstance(program, KleisliProgramCall):
        return program.to_generator()
    if _has_real_method(program, "to_generator"):
        return program.to_generator()
    if isinstance(program, ProgramBase):
        def single_yield():
            yield program
            return None
        return single_yield()
    raise InterpreterInvariantError(f"Cannot convert {type(program).__name__} to generator")


def step(task: TaskState, handlers: dict[type, Handler]) -> TaskState:
    control = task.control
    env = task.env
    store = task.store
    k = task.kontinuation

    if isinstance(control, ValueControl) and not k:
        return task.with_status(DoneStatus(Ok(control.v)))

    if isinstance(control, ErrorControl) and not k:
        ex = control.ex
        if isinstance(ex, Exception):
            return task.with_status(DoneStatus(Err(ex)))
        raise ex

    if isinstance(control, EffectControl):
        effect = control.effect
        from doeff.effects import (
            GatherEffect,
            InterceptEffect,
            LocalEffect,
            ResultSafeEffect,
            WriterListenEffect,
        )

        if isinstance(effect, LocalEffect):
            new_env = env | FrozenDict(effect.env_update)
            return TaskState(
                control=ProgramControl(effect.sub_program),
                env=new_env,
                store=store,
                kontinuation=[LocalFrame(env)] + k,
                status=ReadyStatus(None),
            )

        if isinstance(effect, InterceptEffect):
            return TaskState(
                control=ProgramControl(effect.program),
                env=env,
                store=store,
                kontinuation=[InterceptFrame(effect.transforms)] + k,
                status=ReadyStatus(None),
            )

        if isinstance(effect, WriterListenEffect):
            log_start = len(store.get("__log__", []))
            return TaskState(
                control=ProgramControl(effect.sub_program),
                env=env,
                store=store,
                kontinuation=[ListenFrame(log_start)] + k,
                status=ReadyStatus(None),
            )

        if isinstance(effect, GatherEffect):
            programs = list(effect.programs)
            if not programs:
                return TaskState(
                    control=ValueControl([]),
                    env=env,
                    store=store,
                    kontinuation=k,
                    status=ReadyStatus(None),
                )
            first, *rest = programs
            return TaskState(
                control=ProgramControl(first),
                env=env,
                store=store,
                kontinuation=[GatherFrame(rest, [], env)] + k,
                status=ReadyStatus(None),
            )

        if isinstance(effect, ResultSafeEffect):
            return TaskState(
                control=ProgramControl(effect.sub_program),
                env=env,
                store=store,
                kontinuation=[SafeFrame(env)] + k,
                status=ReadyStatus(None),
            )

        handler = handlers.get(type(effect))
        if handler:
            return handler(effect, k, env, store)

        raise UnhandledEffectError(f"No handler for {type(effect).__name__}")

    if isinstance(control, ProgramControl):
        program = control.program
        from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
        from doeff.program import KleisliProgramCall, ProgramBase
        from doeff.types import EffectBase as EffectBaseType

        pre_captured = None
        try:
            gen = to_generator(program)
            program_call = program if isinstance(program, KleisliProgramCall) else None
            pre_captured = pre_capture_generator(gen, is_resumed=False, program_call=program_call)
            item = next(gen)

            if isinstance(item, EffectBaseType):
                new_control = EffectControl(item)
            elif isinstance(item, ProgramBase):
                new_control = ProgramControl(item)
            else:
                raise InterpreterInvariantError(
                    f"Program yielded unexpected type: {type(item).__name__}. "
                    "Programs must yield Effect or Program instances only."
                )

            return TaskState(
                control=new_control,
                env=env,
                store=store,
                kontinuation=[ReturnFrame(gen, env, program_call=program_call)] + k,
                status=ReadyStatus(None),
            )
        except StopIteration as e:
            return TaskState(
                control=ValueControl(e.value),
                env=env,
                store=store,
                kontinuation=k,
                status=ReadyStatus(None),
            )
        except Exception as ex:
            captured = capture_traceback_safe(k, ex, pre_captured=pre_captured)
            return TaskState(
                control=ErrorControl(ex, captured_traceback=captured),
                env=env,
                store=store,
                kontinuation=k,
                status=ReadyStatus(None),
            )

    if isinstance(control, ValueControl) and k:
        frame = k[0]
        k_rest = k[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase
            from doeff.types import EffectBase as EffectBaseType

            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )

            try:
                item = frame.generator.send(control.v)

                if isinstance(item, EffectBaseType):
                    new_control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    new_control = ProgramControl(item)
                else:
                    raise InterpreterInvariantError(
                        f"Program yielded unexpected type: {type(item).__name__}. "
                        "Programs must yield Effect or Program instances only."
                    )

                return TaskState(
                    control=new_control,
                    env=frame.saved_env,
                    store=store,
                    kontinuation=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + k_rest,
                    status=ReadyStatus(None),
                )
            except StopIteration as e:
                return TaskState(
                    control=ValueControl(e.value),
                    env=frame.saved_env,
                    store=store,
                    kontinuation=k_rest,
                    status=ReadyStatus(None),
                )
            except Exception as ex:
                captured = capture_traceback_safe(k_rest, ex, pre_captured=pre_captured)
                return TaskState(
                    control=ErrorControl(ex, captured_traceback=captured),
                    env=frame.saved_env,
                    store=store,
                    kontinuation=k_rest,
                    status=ReadyStatus(None),
                )

        if isinstance(frame, LocalFrame):
            return TaskState(
                control=ValueControl(control.v),
                env=frame.restore_env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, InterceptFrame):
            return TaskState(
                control=ValueControl(control.v),
                env=env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, ListenFrame):
            from doeff._types_internal import ListenResult
            from doeff.utils import BoundedLog
            
            current_log = store.get("__log__", [])
            captured = current_log[frame.log_start_index:]
            listen_result = ListenResult(value=control.v, log=BoundedLog(captured))
            return TaskState(
                control=ValueControl(listen_result),
                env=env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, GatherFrame):
            if not frame.remaining_programs:
                final_results = frame.collected_results + [control.v]
                return TaskState(
                    control=ValueControl(final_results),
                    env=frame.saved_env,
                    store=store,
                    kontinuation=k_rest,
                    status=ReadyStatus(None),
                )

            next_prog, *rest = frame.remaining_programs
            return TaskState(
                control=ProgramControl(next_prog),
                env=frame.saved_env,
                store=store,
                kontinuation=[GatherFrame(rest, frame.collected_results + [control.v], frame.saved_env)] + k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, SafeFrame):
            return TaskState(
                control=ValueControl(Ok(control.v)),
                env=frame.saved_env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

    if isinstance(control, ErrorControl) and k:
        frame = k[0]
        k_rest = k[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase
            from doeff.types import EffectBase as EffectBaseType

            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )

            try:
                item = frame.generator.throw(control.ex)

                if isinstance(item, EffectBaseType):
                    new_control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    new_control = ProgramControl(item)
                else:
                    raise InterpreterInvariantError(
                        f"Program yielded unexpected type: {type(item).__name__}. "
                        "Programs must yield Effect or Program instances only."
                    )

                return TaskState(
                    control=new_control,
                    env=frame.saved_env,
                    store=store,
                    kontinuation=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + k_rest,
                    status=ReadyStatus(None),
                )
            except StopIteration as e:
                return TaskState(
                    control=ValueControl(e.value),
                    env=frame.saved_env,
                    store=store,
                    kontinuation=k_rest,
                    status=ReadyStatus(None),
                )
            except Exception as propagated:
                if propagated is control.ex:
                    return TaskState(
                        control=ErrorControl(propagated, captured_traceback=control.captured_traceback),
                        env=frame.saved_env,
                        store=store,
                        kontinuation=k_rest,
                        status=ReadyStatus(None),
                    )
                captured = capture_traceback_safe(k_rest, propagated, pre_captured=pre_captured)
                return TaskState(
                    control=ErrorControl(propagated, captured_traceback=captured),
                    env=frame.saved_env,
                    store=store,
                    kontinuation=k_rest,
                    status=ReadyStatus(None),
                )

        if isinstance(frame, LocalFrame):
            return TaskState(
                control=ErrorControl(control.ex, captured_traceback=control.captured_traceback),
                env=frame.restore_env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, InterceptFrame):
            return TaskState(
                control=ErrorControl(control.ex, captured_traceback=control.captured_traceback),
                env=env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, ListenFrame):
            return TaskState(
                control=ErrorControl(control.ex, captured_traceback=control.captured_traceback),
                env=env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, GatherFrame):
            return TaskState(
                control=ErrorControl(control.ex, captured_traceback=control.captured_traceback),
                env=frame.saved_env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

        if isinstance(frame, SafeFrame):
            from doeff._vendor import NOTHING, Some
            from doeff.cesk_traceback import capture_traceback_safe

            ex = control.ex
            if not isinstance(ex, Exception):
                return TaskState(
                    control=ErrorControl(ex, captured_traceback=control.captured_traceback),
                    env=frame.saved_env,
                    store=store,
                    kontinuation=k_rest,
                    status=ReadyStatus(None),
                )

            if control.captured_traceback is not None:
                captured_maybe = Some(control.captured_traceback)
            else:
                captured = capture_traceback_safe(k_rest, ex)
                captured_maybe = Some(captured) if captured else NOTHING
            err_result = Err(ex, captured_traceback=captured_maybe)
            return TaskState(
                control=ValueControl(err_result),
                env=frame.saved_env,
                store=store,
                kontinuation=k_rest,
                status=ReadyStatus(None),
            )

    head_desc = type(k[0]).__name__ if k else "empty"
    raise InterpreterInvariantError(f"Unhandled state: control={type(control).__name__}, K head={head_desc}")


__all__ = [
    "step",
    "Handler",
    "StepError",
    "UnhandledEffectError",
    "InterpreterInvariantError",
    "to_generator",
]
