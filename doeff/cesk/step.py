"""The CESK machine step function for the extensible handler system.

This module provides the step function that implements handler-based effect dispatch.
ALL effects are dispatched through handlers - no hardcoded isinstance checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.errors import InterpreterInvariantError, UnhandledEffectError
from doeff.cesk.frames import (
    ContinueError,
    ContinueProgram,
    ContinueValue,
    InterceptFrame,
    ReturnFrame,
    SafeFrame,
    SuspendOn,
)
from doeff.cesk.handler_frame import (
    HandlerContext,
    HandlerFrame,
    HandlerResultFrame,
    WithHandler,
)
from doeff.cesk.helpers import to_generator
from doeff.cesk.result import Done, Failed, StepResult, Suspended
from doeff.cesk.state import (
    CESKState,
    EffectControl,
    Error,
    ProgramControl,
    Value,
)

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store


def _get_current_handler_depth(k: list[Any]) -> int:
    """Get the number of handlers to skip when dispatching an effect.
    
    If we're inside a handler's program (marked by HandlerResultFrame), we need to skip
    past that handler to find the next outer handler. We skip 1 handler (the one
    associated with the HandlerResultFrame).
    """
    for frame in k:
        if isinstance(frame, HandlerResultFrame):
            return 1
    return 0


def _find_handler_in_k(
    k: list[Any],
    start_depth: int = 0,
) -> tuple[HandlerFrame, int, list[Any], int] | None:
    """Find the first HandlerFrame in K starting from start_depth.
    
    Returns (handler_frame, depth, delimited_k, handler_idx) or None if no handler found.
    The delimited_k is the continuation from the effect site up to (but not including)
    the handler frame. handler_idx is the index in K where the handler was found.
    
    When start_depth > 0, we skip that many HandlerFrames. Skipped handlers are added
    to delimited_k so they're preserved for when the outer handler resumes.
    """
    import os
    debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
    if debug:
        print(f"[_find_handler_in_k] k len={len(k)}, start_depth={start_depth}, k_types={[type(f).__name__ for f in k[:10]]}")
    handlers_to_skip = start_depth
    current_depth = 0
    delimited_k: list[Any] = []

    for i, frame in enumerate(k):
        if isinstance(frame, HandlerFrame):
            if handlers_to_skip > 0:
                handlers_to_skip -= 1
                current_depth += 1
                delimited_k.append(frame)
                if debug:
                    print(f"[_find_handler_in_k] skipped HandlerFrame at {i}")
            else:
                if debug:
                    print(f"[_find_handler_in_k] found target HandlerFrame at {i}, delimited_k len={len(delimited_k)}")
                return (frame, current_depth, delimited_k, i)
        else:
            delimited_k.append(frame)

    return None


def _check_intercept_frames(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: list[Any],
) -> CESKState | None:
    """Check for InterceptFrame in K and apply transforms if found.
    
    Returns a new CESKState if the effect was intercepted, None otherwise.
    """
    from doeff.cesk.frames import InterceptBypassFrame
    from doeff.program import ProgramBase

    for i, frame in enumerate(k):
        if isinstance(frame, HandlerFrame):
            break
        if isinstance(frame, InterceptFrame):
            for transform in frame.transforms:
                result = transform(effect)
                if result is not effect and result is not None:
                    k_rest = k[i + 1:]
                    if isinstance(result, ProgramBase):
                        return CESKState(
                            C=ProgramControl(result),
                            E=env,
                            S=store,
                            K=[frame] + k_rest,
                        )
                    return CESKState(
                        C=EffectControl(result),
                        E=env,
                        S=store,
                        K=k[:i + 1] + k_rest,
                    )
        if isinstance(frame, InterceptBypassFrame):
            if frame.effect_id == id(effect) and frame.intercept_frame in k[i + 1:]:
                continue

    return None


def _invoke_handler(
    handler_frame: HandlerFrame,
    handler_depth: int,
    effect: EffectBase,
    delimited_k: list[Any],
    env: Environment,
    store: Store,
    k_after_handler: list[Any],
) -> CESKState:
    ctx = HandlerContext(
        store=store,
        env=env,
        delimited_k=delimited_k,
        handler_depth=handler_depth,
        outer_k=[handler_frame] + k_after_handler,
    )

    handler_program = handler_frame.handler(effect, ctx)

    handler_result_frame = HandlerResultFrame(
        original_effect=effect,
        handler_depth=handler_depth,
        handled_program_k=delimited_k,
    )

    new_k = [handler_result_frame] + [handler_frame] + k_after_handler

    return CESKState(
        C=ProgramControl(handler_program),
        E=env,
        S=store,
        K=new_k,
    )


def _make_suspended_from_suspend_on(suspend_on: SuspendOn) -> Suspended:
    from doeff.cesk.handlers.queue_handler import (
        CURRENT_TASK_KEY,
        PENDING_IO_KEY,
    )
    from doeff.effects.future import AllTasksSuspendedEffect, FutureAwaitEffect

    stored_k = suspend_on.stored_k or []
    stored_env = suspend_on.stored_env or {}
    stored_store = suspend_on.stored_store or {}

    if suspend_on.awaitable is None:
        pending_io = stored_store.get(PENDING_IO_KEY, {})
        effect: Any = AllTasksSuspendedEffect(pending_io=pending_io, store=stored_store)

        def resume_multi(value: Any, new_store: Store) -> CESKState:
            task_id, result = value

            task_info = pending_io.get(task_id)
            if task_info is None:
                raise RuntimeError(f"Task {task_id} not found in pending_io")

            task_k = task_info["k"]
            task_store_snapshot = task_info.get("store_snapshot", {})

            new_pending = dict(pending_io)
            del new_pending[task_id]

            merged_store = dict(task_store_snapshot)
            for key, val in stored_store.items():
                if isinstance(key, str) and key.startswith("__scheduler_"):
                    merged_store[key] = val
            merged_store[PENDING_IO_KEY] = new_pending
            merged_store[CURRENT_TASK_KEY] = task_id

            return CESKState(
                C=Value(result),
                E=stored_env,
                S=merged_store,
                K=task_k,
            )

        def resume_error_multi(error: BaseException) -> CESKState:
            return CESKState(
                C=Error(error),
                E=stored_env,
                S=stored_store,
                K=stored_k,
            )

        return Suspended(
            effect=effect,
            resume=resume_multi,
            resume_error=resume_error_multi,
        )
    effect = FutureAwaitEffect(awaitable=suspend_on.awaitable)

    def resume_single(value: Any, new_store: Store) -> CESKState:
        merged_store = dict(new_store)
        for key, val in stored_store.items():
            if key not in merged_store:
                merged_store[key] = val
        return CESKState(
            C=Value(value),
            E=stored_env,
            S=merged_store,
            K=stored_k,
        )

    def resume_error_single(error: BaseException) -> CESKState:
        return CESKState(
            C=Error(error),
            E=stored_env,
            S=stored_store,
            K=stored_k,
        )

    return Suspended(
        effect=effect,
        resume=resume_single,
        resume_error=resume_error_single,
    )


def step(state: CESKState) -> StepResult:
    """Step the CESK machine with handler-based effect dispatch.
    
    This step function:
    1. Handles WithHandler by pushing HandlerFrame onto K
    2. Dispatches ALL effects through handlers (walks K to find HandlerFrame)
    3. Interprets handler results (ContinueValue, ContinueError, ResumeK)
    """
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, Value) and not K:
        import os
        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
        if debug:
            print(f"[step] Done: C.v type = {type(C.v).__name__}, value = {str(C.v)[:100]}")
        return Done(C.v, S)

    if isinstance(C, Error) and not K:
        return Failed(C.ex, S, captured_traceback=C.captured_traceback)

    if isinstance(C, EffectControl):
        effect = C.effect

        import os
        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
        if debug:
            print(f"[step] EffectControl: {type(effect).__name__}")

        if isinstance(effect, WithHandler):
            handler_frame = HandlerFrame(
                handler=effect.handler,
                saved_env=E,
            )
            return CESKState(
                C=ProgramControl(effect.program),
                E=E,
                S=S,
                K=[handler_frame] + K,
            )

        from doeff.effects.pure import PureEffect
        if isinstance(effect, PureEffect):
            value = effect.value
            store = S
            if (isinstance(value, ContinueValue) and value.store is not None) or (isinstance(value, ContinueError) and value.store is not None):
                store = value.store
            return CESKState(C=Value(value), E=E, S=store, K=K)

        intercept_result = _check_intercept_frames(effect, E, S, K)
        if intercept_result is not None:
            return intercept_result

        handler_search_depth = _get_current_handler_depth(K)
        handler_info = _find_handler_in_k(K, handler_search_depth)

        if handler_info is None:
            from doeff.cesk_traceback import capture_traceback_safe
            unhandled_ex = UnhandledEffectError(f"No handler for {type(effect).__name__}")
            captured = capture_traceback_safe(K, unhandled_ex)
            return CESKState(
                C=Error(unhandled_ex, captured_traceback=captured),
                E=E,
                S=S,
                K=K,
            )

        handler_frame, handler_depth, delimited_k, handler_idx = handler_info
        k_after_handler = K[handler_idx + 1:]

        return _invoke_handler(
            handler_frame=handler_frame,
            handler_depth=handler_depth,
            effect=effect,
            delimited_k=delimited_k,
            env=E,
            store=S,
            k_after_handler=k_after_handler,
        )

    if isinstance(C, ProgramControl):
        program = C.program
        from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
        from doeff.program import KleisliProgramCall, ProgramBase

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
                    C=Error(
                        InterpreterInvariantError(
                            f"Program yielded unexpected type: {type(item).__name__}. "
                            "Programs must yield Effect or Program instances only."
                        )
                    ),
                    E=E,
                    S=S,
                    K=K,
                )

            kleisli_fn_name: str | None = None
            kleisli_filename: str | None = None
            kleisli_lineno: int | None = None
            if program_call is not None:
                kleisli_source = getattr(program_call, "kleisli_source", None)
                original_func = getattr(kleisli_source, "original_func", None) if kleisli_source else None
                if original_func is not None and hasattr(original_func, "__code__"):
                    code = original_func.__code__
                    kleisli_fn_name = program_call.function_name
                    kleisli_filename = code.co_filename
                    kleisli_lineno = code.co_firstlineno

            return CESKState(
                C=control,
                E=E,
                S=S,
                K=[ReturnFrame(
                    gen, E,
                    program_call=program_call,
                    kleisli_function_name=kleisli_fn_name,
                    kleisli_filename=kleisli_filename,
                    kleisli_lineno=kleisli_lineno,
                )] + K,
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
                        C=Error(
                            InterpreterInvariantError(
                                f"Program yielded unexpected type: {type(item).__name__}. "
                                "Programs must yield Effect or Program instances only."
                            )
                        ),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(
                        frame.generator, frame.saved_env,
                        program_call=frame.program_call,
                        kleisli_function_name=frame.kleisli_function_name,
                        kleisli_filename=frame.kleisli_filename,
                        kleisli_lineno=frame.kleisli_lineno,
                    )] + K_rest,
                )
            except StopIteration as e:
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as ex:
                captured = capture_traceback_safe(K_rest, ex, pre_captured=pre_captured)
                return CESKState(
                    C=Error(ex, captured_traceback=captured), E=frame.saved_env, S=S, K=K_rest
                )

        if isinstance(frame, HandlerFrame):
            result = frame.on_value(C.v, E, S, K_rest)
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            raise InterpreterInvariantError(f"Unexpected HandlerFrame result: {type(result)}")

        if isinstance(frame, HandlerResultFrame):
            result = frame.on_value(C.v, E, S, K_rest)
            import os
            debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
            if debug:
                print(f"[step] HandlerResultFrame.on_value returned: {type(result).__name__}, value_type={type(C.v).__name__}")
            if isinstance(result, ContinueValue):
                if debug:
                    print(f"[step] ContinueValue: k_len={len(result.k)}, value={str(result.value)[:50]}")
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            if isinstance(result, ContinueProgram):
                return CESKState(
                    C=ProgramControl(result.program),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            if isinstance(result, SuspendOn):
                if debug:
                    print(f"[step] SuspendOn: awaitable={result.awaitable}, k_len={len(result.stored_k or [])}")
                return _make_suspended_from_suspend_on(result)
            raise InterpreterInvariantError(f"Unexpected HandlerResultFrame result: {type(result)}")

        from doeff.cesk.frames import Frame
        if isinstance(frame, Frame):
            result = frame.on_value(C.v, E, S, K_rest)
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            if isinstance(result, ContinueProgram):
                return CESKState(
                    C=ProgramControl(result.program),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            raise InterpreterInvariantError(f"Unexpected Frame result: {type(result)}")

    if isinstance(C, Error) and K:
        frame = K[0]
        K_rest = K[1:]

        if isinstance(frame, ReturnFrame):
            from doeff.cesk_traceback import capture_traceback_safe, pre_capture_generator
            from doeff.program import ProgramBase

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
                        C=Error(
                            InterpreterInvariantError(
                                f"Program yielded unexpected type: {type(item).__name__}. "
                                "Programs must yield Effect or Program instances only."
                            )
                        ),
                        E=frame.saved_env,
                        S=S,
                        K=K_rest,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[ReturnFrame(
                        frame.generator, frame.saved_env,
                        program_call=frame.program_call,
                        kleisli_function_name=frame.kleisli_function_name,
                        kleisli_filename=frame.kleisli_filename,
                        kleisli_lineno=frame.kleisli_lineno,
                    )] + K_rest,
                )
            except StopIteration as e:
                return CESKState(C=Value(e.value), E=frame.saved_env, S=S, K=K_rest)
            except Exception as propagated:
                captured = capture_traceback_safe(K_rest, propagated, pre_captured=pre_captured)
                return CESKState(
                    C=Error(propagated, captured_traceback=captured),
                    E=frame.saved_env,
                    S=S,
                    K=K_rest,
                )

        if isinstance(frame, HandlerFrame):
            result = frame.on_error(C.ex, E, S, K_rest)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            raise InterpreterInvariantError(f"Unexpected HandlerFrame error result: {type(result)}")

        if isinstance(frame, HandlerResultFrame):
            result = frame.on_error(C.ex, E, S, K_rest)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            raise InterpreterInvariantError(f"Unexpected HandlerResultFrame error result: {type(result)}")

        if isinstance(frame, SafeFrame):
            result = frame.on_error(C.ex, E, S, K_rest)
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            raise InterpreterInvariantError(f"Unexpected SafeFrame error result: {type(result)}")

        from doeff.cesk.frames import Frame
        if isinstance(frame, Frame):
            result = frame.on_error(C.ex, E, S, K_rest)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            raise InterpreterInvariantError(f"Unexpected Frame error result: {type(result)}")

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


__all__ = [
    "step",
]
