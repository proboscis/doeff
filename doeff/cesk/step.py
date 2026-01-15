"""The CESK machine step function."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._vendor import NOTHING, Err, FrozenDict, Ok, Some
from doeff._types_internal import EffectBase, ListenResult
from doeff.cesk.types import Store
from doeff.cesk.frames import (
    CatchFrame,
    FinallyFrame,
    GatherFrame,
    InterceptFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.state import CESKState, EffectControl, Error, ProgramControl, Value
from doeff.cesk.result import Done, Failed, StepResult, Suspended
from doeff.cesk.dispatcher import InterpreterInvariantError, ScheduledEffectDispatcher, UnhandledEffectError
from doeff.cesk.classification import (
    has_intercept_frame,
    is_control_flow_effect,
    is_effectful,
    is_pure_effect,
)
from doeff.cesk.helpers import (
    _wrap_callable_as_program,
    apply_intercept_chain,
    make_cleanup_then_raise,
    make_cleanup_then_return,
    to_generator,
)
from doeff.utils import BoundedLog

if TYPE_CHECKING:
    from doeff.program import Program


def step(state: CESKState, dispatcher: ScheduledEffectDispatcher | None = None) -> StepResult:
    """Single step of the CESK machine."""
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, Value) and not K:
        return Done(C.v, S)

    if isinstance(C, Error) and not K:
        return Failed(C.ex, S, captured_traceback=C.captured_traceback)

    if isinstance(C, EffectControl):
        effect = C.effect
        from doeff.effects import (
            GatherEffect,
            InterceptEffect,
            LocalEffect,
            ResultCatchEffect,
            ResultFailEffect,
            ResultFinallyEffect,
            ResultSafeEffect,
            WriterListenEffect,
        )

        if isinstance(effect, ResultFailEffect):
            return CESKState(C=Error(effect.exception), E=E, S=S, K=K)

        if isinstance(effect, ResultCatchEffect):
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[CatchFrame(effect.handler, E)] + K,
            )

        if isinstance(effect, ResultFinallyEffect):
            cleanup = effect.finalizer
            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            if not isinstance(cleanup, (ProgramBase, EffectBase)):
                if callable(cleanup):
                    cleanup = _wrap_callable_as_program(cleanup)
                else:
                    from doeff.program import Program
                    cleanup = Program.pure(cleanup)
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[FinallyFrame(cleanup, E)] + K,
            )

        if isinstance(effect, LocalEffect):
            new_env = E | FrozenDict(effect.env_update)
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=new_env,
                S=S,
                K=[LocalFrame(E)] + K,
            )

        if isinstance(effect, InterceptEffect):
            return CESKState(
                C=ProgramControl(effect.program),
                E=E,
                S=S,
                K=[InterceptFrame(effect.transforms)] + K,
            )

        if isinstance(effect, WriterListenEffect):
            log_start = len(S.get("__log__", []))
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[ListenFrame(log_start)] + K,
            )

        if isinstance(effect, GatherEffect):
            programs = list(effect.programs)
            if not programs:
                return CESKState(C=Value([]), E=E, S=S, K=K)
            first, *rest = programs
            return CESKState(
                C=ProgramControl(first),
                E=E,
                S=S,
                K=[GatherFrame(rest, [], E)] + K,
            )

        if isinstance(effect, ResultSafeEffect):
            return CESKState(
                C=ProgramControl(effect.sub_program),
                E=E,
                S=S,
                K=[SafeFrame(E)] + K,
            )

        if not is_control_flow_effect(effect) and has_intercept_frame(K):
            from doeff.cesk_traceback import capture_traceback_safe

            try:
                transformed = apply_intercept_chain(K, effect)
            except Exception as ex:
                captured = capture_traceback_safe(K, ex)
                return CESKState(C=Error(ex, captured_traceback=captured), E=E, S=S, K=K)

            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            if isinstance(transformed, EffectBase):
                if is_control_flow_effect(transformed):
                    return CESKState(C=EffectControl(transformed), E=E, S=S, K=K)

                has_handler = dispatcher.has_handler(transformed) if dispatcher else (is_pure_effect(transformed) or is_effectful(transformed))

                if has_handler:
                    return Suspended(
                        effect=transformed,
                        resume=lambda v, new_store, E=E, K=K: CESKState(
                            C=Value(v), E=E, S=new_store, K=K
                        ),
                        resume_error=lambda ex, E=E, S=S, K=K: CESKState(
                            C=Error(ex), E=E, S=S, K=K
                        ),
                    )

                unhandled_ex = UnhandledEffectError(f"No handler for {type(transformed).__name__}")
                captured = capture_traceback_safe(K, unhandled_ex)
                return CESKState(
                    C=Error(unhandled_ex, captured_traceback=captured),
                    E=E,
                    S=S,
                    K=K,
                )

            if isinstance(transformed, ProgramBase):
                return CESKState(C=ProgramControl(transformed), E=E, S=S, K=K)

            unknown_ex = UnhandledEffectError(f"No handler for {type(transformed).__name__}")
            captured = capture_traceback_safe(K, unknown_ex)
            return CESKState(
                C=Error(unknown_ex, captured_traceback=captured),
                E=E,
                S=S,
                K=K,
            )

        has_handler = dispatcher.has_handler(effect) if dispatcher else (is_pure_effect(effect) or is_effectful(effect))

        if has_handler:
            return Suspended(
                effect=effect,
                resume=lambda v, new_store, E=E, K=K: CESKState(
                    C=Value(v), E=E, S=new_store, K=K
                ),
                resume_error=lambda ex, E=E, S=S, K=K: CESKState(
                    C=Error(ex), E=E, S=S, K=K
                ),
            )

        from doeff.cesk_traceback import capture_traceback_safe

        unhandled_ex = UnhandledEffectError(f"No handler for {type(effect).__name__}")
        captured = capture_traceback_safe(K, unhandled_ex)
        return CESKState(
            C=Error(unhandled_ex, captured_traceback=captured),
            E=E,
            S=S,
            K=K,
        )

    if isinstance(C, ProgramControl):
        program = C.program
        from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
        from doeff.program import KleisliProgramCall, ProgramBase
        from doeff.types import EffectBase

        pre_captured = None
        try:
            gen = to_generator(program)
            program_call = program if isinstance(program, KleisliProgramCall) else None
            pre_captured = pre_capture_generator(gen, is_resumed=False, program_call=program_call)
            item = next(gen)

            if isinstance(item, EffectBase):
                control = EffectControl(item)
            elif isinstance(item, ProgramBase):
                control = ProgramControl(item)
            else:
                return CESKState(
                    C=Error(InterpreterInvariantError(f"Program yielded unexpected type: {type(item).__name__}. Programs must yield Effect or Program instances only.")),
                    E=E,
                    S=S,
                    K=K,
                )

            return CESKState(
                C=control,
                E=E,
                S=S,
                K=[ReturnFrame(gen, E, program_call=program_call)] + K,
            )
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K)
        except Exception as ex:
            captured = capture_traceback_safe(K, ex, pre_captured=pre_captured)
            return CESKState(C=Error(ex, captured_traceback=captured), E=E, S=S, K=K)

    if isinstance(C, Value) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )

            try:
                item = frame.generator.send(C.v)

                if isinstance(item, EffectBase):
                    control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    control = ProgramControl(item)
                else:
                    return CESKState(
                        C=Error(InterpreterInvariantError(f"Program yielded unexpected type: {type(item).__name__}. Programs must yield Effect or Program instances only.")),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
                )
            except StopIteration as e:
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as ex:
                captured = capture_traceback_safe(K_rest, ex, pre_captured=pre_captured)
                return CESKState(C=Error(ex, captured_traceback=captured), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, CatchFrame):
            return CESKState(C=Value(C.v), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, FinallyFrame):
            cleanup_program = make_cleanup_then_return(frame.cleanup_program, C.v)
            return CESKState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            return CESKState(C=Value(C.v), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            return CESKState(C=Value(C.v), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            current_log = S.get("__log__", [])
            captured = current_log[frame.log_start_index:]
            listen_result = ListenResult(value=C.v, log=BoundedLog(captured))
            return CESKState(C=Value(listen_result), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            if not frame.remaining_programs:
                final_results = frame.collected_results + [C.v]
                return CESKState(C=Value(final_results), E=frame.saved_env, S=S, K=K_rest)

            next_prog, *rest = frame.remaining_programs
            return CESKState(
                C=ProgramControl(next_prog),
                E=frame.saved_env,
                S=S,
                K=[GatherFrame(rest, frame.collected_results + [C.v], frame.saved_env)] + K_rest,
            )

        if isinstance(frame, SafeFrame):
            return CESKState(C=Value(Ok(C.v)), E=frame.saved_env, S=S, K=K_rest)

    if isinstance(C, Error) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase
            from doeff.types import EffectBase

            pre_captured = pre_capture_generator(
                frame.generator, is_resumed=True, program_call=frame.program_call
            )

            try:
                item = frame.generator.throw(C.ex)

                if isinstance(item, EffectBase):
                    control = EffectControl(item)
                elif isinstance(item, ProgramBase):
                    control = ProgramControl(item)
                else:
                    return CESKState(
                        C=Error(InterpreterInvariantError(f"Program yielded unexpected type: {type(item).__name__}. Programs must yield Effect or Program instances only.")),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
                )
            except StopIteration as e:
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as propagated:
                if propagated is C.ex:
                    return CESKState(
                        C=Error(propagated, captured_traceback=C.captured_traceback),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )
                captured = capture_traceback_safe(K_rest, propagated, pre_captured=pre_captured)
                return CESKState(
                    C=Error(propagated, captured_traceback=captured),
                    E=frame.saved_env,
                    S=S,
                    K=K_rest,
                )

        if isinstance(frame, CatchFrame):
            from doeff.cesk_traceback import capture_traceback_safe

            try:
                recovery_result = frame.handler(C.ex)
                from doeff.program import Program, ProgramBase
                from doeff.types import EffectBase

                if isinstance(recovery_result, (ProgramBase, EffectBase)):
                    recovery_program = recovery_result
                else:
                    recovery_program = Program.pure(recovery_result)
                return CESKState(C=ProgramControl(recovery_program), E=frame.saved_env, S=S, K=K_rest)
            except Exception as handler_ex:
                captured = capture_traceback_safe(K_rest, handler_ex)
                return CESKState(C=Error(handler_ex, captured_traceback=captured), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, FinallyFrame):
            cleanup_program = make_cleanup_then_raise(frame.cleanup_program, C.ex)
            return CESKState(C=ProgramControl(cleanup_program), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, LocalFrame):
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=frame.restore_env, S=S, K=K_rest)

        if isinstance(frame, InterceptFrame):
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=E, S=S, K=K_rest)

        if isinstance(frame, ListenFrame):
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=E, S=S, K=K_rest)

        if isinstance(frame, GatherFrame):
            return CESKState(C=Error(C.ex, captured_traceback=C.captured_traceback), E=frame.saved_env, S=S, K=K_rest)

        if isinstance(frame, SafeFrame):
            from doeff.cesk_traceback import capture_traceback_safe

            if C.captured_traceback is not None:
                captured_maybe = Some(C.captured_traceback)
            else:
                captured = capture_traceback_safe(K_rest, C.ex)
                captured_maybe = Some(captured) if captured else NOTHING
            err_result = Err(C.ex, captured_traceback=captured_maybe)
            return CESKState(C=Value(err_result), E=frame.saved_env, S=S, K=K_rest)

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


__all__ = [
    "step",
]
