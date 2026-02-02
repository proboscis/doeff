"""The CESK machine step function for the extensible handler system.

This module provides the step function that implements handler-based effect dispatch.
ALL effects are dispatched through handlers - no hardcoded isinstance checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.errors import InterpreterInvariantError, UnhandledEffectError
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import (
    HandlerContext,
    HandlerFrame,
    HandlerResultFrame,
    WithHandler,
)
from doeff.cesk.helpers import to_generator
from doeff.cesk.result import Done, Failed, PythonAsyncSyntaxEscape, StepResult
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
    past that handler to find the next outer handler. However, if there are HandlerFrames
    BEFORE the HandlerResultFrame, those are nested handlers that should NOT be skipped.

    The logic: count consecutive HandlerResultFrames at the start of K, stopping when
    we hit a HandlerFrame. Each HandlerResultFrame indicates a handler we're currently
    executing inside of and should skip when forwarding effects.

    If we encounter a HandlerFrame first, it means we're inside that handler's sub-program,
    and effects should go to that handler (no skip needed).
    """
    depth = 0
    for frame in k:
        if isinstance(frame, HandlerFrame):
            # Found a HandlerFrame - stop counting, skip 'depth' handlers
            return depth
        if isinstance(frame, HandlerResultFrame):
            # Found a HandlerResultFrame - increment depth, keep looking
            depth += 1
    return depth


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
                    handler_name = getattr(frame.handler, "__name__", str(frame.handler)[:50])
                    print(f"[_find_handler_in_k] skipped HandlerFrame at {i}, handler={handler_name}")
            else:
                if debug:
                    handler_name = getattr(frame.handler, "__name__", str(frame.handler)[:50])
                    print(f"[_find_handler_in_k] found target HandlerFrame at {i}, handler={handler_name}, delimited_k len={len(delimited_k)}")
                return (frame, current_depth, delimited_k, i)
        else:
            delimited_k.append(frame)

    return None


def _invoke_handler(
    handler_frame: HandlerFrame,
    handler_depth: int,
    effect: EffectBase,
    delimited_k: list[Any],
    env: Environment,
    store: Store,
    k_after_handler: list[Any],
    inherited_handlers: list[Any],
) -> CESKState:
    ctx = HandlerContext(
        store=store,
        env=env,
        delimited_k=delimited_k,
        handler_depth=handler_depth,
        outer_k=[handler_frame] + k_after_handler,
        inherited_handlers=inherited_handlers,
    )

    handler_program = handler_frame.handler(effect, ctx)

    handler_result_frame = HandlerResultFrame(
        original_effect=effect,
        handler_depth=handler_depth,
        handled_program_k=delimited_k,
        inherited_handlers=inherited_handlers,
    )

    new_k = [handler_result_frame] + [handler_frame] + k_after_handler

    return CESKState(
        C=ProgramControl(handler_program),
        E=env,
        S=store,
        K=new_k,
    )


def step(state: CESKState) -> StepResult:
    """Step the CESK machine with handler-based effect dispatch.
    
    This step function:
    1. Handles WithHandler by pushing HandlerFrame onto K
    2. Dispatches ALL effects through handlers (walks K to find HandlerFrame)
    3. Interprets handler results (CESKState, ResumeK)
    """
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, Value) and not K:
        import os
        debug = os.environ.get("DOEFF_DEBUG", "").lower() in ("1", "true", "yes")
        if debug:
            print(f"[step] Done: C.v type = {type(C.v).__name__}, value = {str(C.v)[:100]}")
        if isinstance(C.v, PythonAsyncSyntaxEscape):
            return C.v
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
            return CESKState(C=Value(effect.value), E=E, S=S, K=K)

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

        # Get inherited_handlers for spawn inheritance
        # We need to capture:
        # 1. HandlerFrames added AFTER the first HRF (dynamically via WithHandler)
        # 2. Original handlers from HRF.inherited_handlers
        #
        # Why not just extract from K? During effect forwarding, inner handlers
        # are moved to delimited_k (stored in HRFs), not in K. So K only has
        # outer handlers. HRF.inherited_handlers preserves the full set.
        inherited_handlers = []
        frames_before_hrf: list[Any] = []
        found_hrf = False

        for frame in K:
            if isinstance(frame, HandlerResultFrame):
                # Found HRF - use its inherited_handlers as base
                inherited_handlers = list(frame.inherited_handlers)
                found_hrf = True
                break
            elif isinstance(frame, HandlerFrame):
                # HandlerFrame before HRF = dynamically added, should be inherited
                frames_before_hrf.append(frame)

        if found_hrf:
            # Prepend dynamically-added handlers to the base inherited_handlers
            inherited_handlers = frames_before_hrf + inherited_handlers
        else:
            # No HRF found - truly first effect, extract from original K
            inherited_handlers = [f for f in K if isinstance(f, HandlerFrame)]

        return _invoke_handler(
            handler_frame=handler_frame,
            handler_depth=handler_depth,
            effect=effect,
            delimited_k=delimited_k,
            env=E,
            store=S,
            k_after_handler=k_after_handler,
            inherited_handlers=inherited_handlers,
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
            # HandlerFrame returns CESKState or PythonAsyncSyntaxEscape directly
            return frame.on_value(C.v, E, S, K_rest)

        if isinstance(frame, HandlerResultFrame):
            # HandlerResultFrame returns CESKState or PythonAsyncSyntaxEscape directly
            return frame.on_value(C.v, E, S, K_rest)

        from doeff.cesk.frames import Frame
        if isinstance(frame, Frame):
            # Frames return CESKState directly
            return frame.on_value(C.v, E, S, K_rest)

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
            # HandlerFrame returns CESKState directly
            return frame.on_error(C.ex, E, S, K_rest)

        if isinstance(frame, HandlerResultFrame):
            # HandlerResultFrame returns CESKState directly
            return frame.on_error(C.ex, E, S, K_rest)

        from doeff.cesk.frames import Frame
        if isinstance(frame, Frame):
            # Frames return CESKState directly
            return frame.on_error(C.ex, E, S, K_rest)

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


__all__ = [
    "step",
]
