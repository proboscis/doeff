"""The CESK machine step function for the extensible handler system.

This module provides the step_v2 function that implements handler-based effect dispatch.
ALL effects are dispatched through handlers - no hardcoded isinstance checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.errors import InterpreterInvariantError, UnhandledEffectError
from doeff.cesk.frames import (
    ContinueError,
    ContinueValue,
    ReturnFrame,
)
from doeff.cesk.handler_frame import (
    HandlerContext,
    HandlerFrame,
    HandlerResultFrame,
    ResumeK,
    WithHandler,
)
from doeff.cesk.helpers import to_generator
from doeff.cesk.result import Done, Failed, StepResult
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
    for frame in k:
        if isinstance(frame, HandlerResultFrame):
            return frame.handler_depth + 1
    return 0


def _find_handler_in_k(
    k: list[Any],
    start_depth: int = 0,
) -> tuple[HandlerFrame, int, list[Any], int] | None:
    """Find the first HandlerFrame in K starting from start_depth.
    
    Returns (handler_frame, depth, delimited_k, handler_idx) or None if no handler found.
    The delimited_k is the continuation from the effect site up to (but not including)
    the handler frame. handler_idx is the index in K where the handler was found.
    """
    current_depth = 0
    delimited_k: list[Any] = []
    
    for i, frame in enumerate(k):
        if isinstance(frame, HandlerFrame):
            if current_depth >= start_depth:
                return (frame, current_depth, delimited_k, i)
            current_depth += 1
            delimited_k = []
        else:
            delimited_k.append(frame)
    
    return None


def _invoke_handler(
    handler_frame: HandlerFrame,
    handler_depth: int,
    effect: EffectBase,
    delimited_k: list[Any],
    env: "Environment",
    store: "Store",
    k_after_handler: list[Any],
) -> CESKState:
    ctx = HandlerContext(
        store=store,
        env=env,
        delimited_k=delimited_k,
        handler_depth=handler_depth,
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


def step_v2(state: CESKState) -> StepResult:
    """Step the CESK machine with handler-based effect dispatch.
    
    This step function:
    1. Handles WithHandler by pushing HandlerFrame onto K
    2. Dispatches ALL effects through handlers (walks K to find HandlerFrame)
    3. Interprets handler results (ContinueValue, ContinueError, ResumeK)
    """
    C, E, S, K = state.C, state.E, state.S, state.K
    
    if isinstance(C, Value) and not K:
        return Done(C.v, S)
    
    if isinstance(C, Error) and not K:
        return Failed(C.ex, S, captured_traceback=C.captured_traceback)
    
    if isinstance(C, EffectControl):
        effect = C.effect
        
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
                    K=[ReturnFrame(frame.generator, frame.saved_env, program_call=frame.program_call)] + K_rest,
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
            elif isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            raise InterpreterInvariantError(f"Unexpected HandlerFrame result: {type(result)}")
        
        if isinstance(frame, HandlerResultFrame):
            result = frame.on_value(C.v, E, S, K_rest)
            if isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            elif isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            raise InterpreterInvariantError(f"Unexpected HandlerResultFrame result: {type(result)}")
    
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
        
        if isinstance(frame, HandlerFrame):
            result = frame.on_error(C.ex, E, S, K_rest)
            if isinstance(result, ContinueError):
                return CESKState(
                    C=Error(result.error, captured_traceback=result.captured_traceback),
                    E=result.env,
                    S=result.store,
                    K=result.k,
                )
            elif isinstance(result, ContinueValue):
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
            elif isinstance(result, ContinueValue):
                return CESKState(C=Value(result.value), E=result.env, S=result.store, K=result.k)
            raise InterpreterInvariantError(f"Unexpected HandlerResultFrame error result: {type(result)}")
    
    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(f"Unhandled state: C={type(C).__name__}, K head={head_desc}")


__all__ = [
    "step_v2",
]
