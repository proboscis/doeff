"""V2 CESK+H step function with true handler/continuation separation.

K = Program continuation only (ReturnFrames for user code)
H = Handler stack (HandlerCtx entries with handler state)
active_handler = Index of currently executing handler (-1 = program running)
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from doeff._types_internal import EffectBase
from doeff.cesk.errors import InterpreterInvariantError, UnhandledEffectError
from doeff.cesk.frames import ReturnFrame
from doeff.cesk.handler_frame import (
    HandlerContext,
    HandlerCtx,
    ResumeK,
    WithHandler,
    get_handler_handles,
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
    from doeff.cesk.frames import Kontinuation
    from doeff.cesk.state import HandlerStack


def _find_handler(
    effect_type: type, H: "HandlerStack", start_idx: int = 0
) -> tuple[int, HandlerCtx] | None:
    """Find handler for effect type in H, starting from start_idx."""
    for i in range(start_idx, len(H)):
        h = H[i]
        # Empty handles = catch-all (handles everything)
        if not h.handles or any(issubclass(effect_type, t) for t in h.handles):
            return (i, h)
    return None


def step(state: CESKState) -> StepResult:
    """V2 CESK+H step function.

    K = Program continuation (user code generators only)
    H = Handler stack (separate from K)
    active_handler = Currently executing handler index (-1 = none)
    """
    C, E, S, K, H, active = state.C, state.E, state.S, state.K, state.H, state.active_handler

    # ========================================
    # Terminal states (empty K, no active handler)
    # ========================================

    if isinstance(C, Value) and not K and active == -1:
        if isinstance(C.v, PythonAsyncSyntaxEscape):
            return C.v
        if isinstance(C.v, ResumeK):
            # ResumeK at top level - switch to the provided continuation
            v = C.v
            result_store = v.store if v.store is not None else S
            result_env = v.env if v.env is not None else E
            if v.error is not None:
                return CESKState(
                    C=Error(v.error),
                    E=result_env,
                    S=result_store,
                    K=list(v.k),
                    H=H,
                    active_handler=-1,
                )
            return CESKState(
                C=Value(v.value), E=result_env, S=result_store, K=list(v.k), H=H, active_handler=-1
            )
        return Done(C.v, S)

    if isinstance(C, Error) and not K and active == -1:
        return Failed(C.ex, S, captured_traceback=C.captured_traceback)

    # ========================================
    # Handler completing (active >= 0, K empty)
    # ========================================

    if isinstance(C, Value) and not K and active >= 0:
        handler_ctx = H[active]
        value = C.v

        # DirectState: pass through unchanged (direct jumps like Local, Listen, etc.)
        # The handler has already constructed the exact state to jump to
        from doeff.cesk.result import DirectState

        if isinstance(value, DirectState):
            inner = value.state
            new_handler = replace(handler_ctx, generator=None, captured_k=None)
            new_H = H[:active] + [new_handler] + H[active + 1 :]
            return CESKState(
                C=inner.C,
                E=inner.E,
                S=inner.S,
                K=inner.K,  # Use K from DirectState as-is
                H=new_H,
                active_handler=-1,
            )

        # Handler returned CESKState - use it to continue
        if isinstance(value, CESKState):
            # Restore K from handler's captured_k, merge with returned state
            restored_k = list(handler_ctx.captured_k) if handler_ctx.captured_k else []
            new_handler = replace(handler_ctx, generator=None, captured_k=None)
            new_H = H[:active] + [new_handler] + H[active + 1 :]
            return CESKState(
                C=value.C,
                E=value.E,
                S=value.S,
                K=restored_k,
                H=new_H,
                active_handler=-1,
            )

        # Handler returned ResumeK - switch continuation
        if isinstance(value, ResumeK):
            new_handler = replace(handler_ctx, generator=None, captured_k=None)
            new_H = H[:active] + [new_handler] + H[active + 1 :]
            result_store = value.store if value.store is not None else S
            result_env = value.env if value.env is not None else E
            if value.error is not None:
                return CESKState(
                    C=Error(value.error),
                    E=result_env,
                    S=result_store,
                    K=list(value.k),
                    H=new_H,
                    active_handler=-1,
                )
            return CESKState(
                C=Value(value.value),
                E=result_env,
                S=result_store,
                K=list(value.k),
                H=new_H,
                active_handler=-1,
            )

        # Handler returned plain value - restore K and send value
        restored_k = list(handler_ctx.captured_k) if handler_ctx.captured_k else []
        new_handler = replace(handler_ctx, generator=None, captured_k=None)
        new_H = H[:active] + [new_handler] + H[active + 1 :]
        return CESKState(
            C=Value(value), E=handler_ctx.saved_env, S=S, K=restored_k, H=new_H, active_handler=-1
        )

    if isinstance(C, Error) and not K and active >= 0:
        # Handler raised error - restore K and propagate error
        handler_ctx = H[active]
        restored_k = list(handler_ctx.captured_k) if handler_ctx.captured_k else []
        new_handler = replace(handler_ctx, generator=None, captured_k=None)
        new_H = H[:active] + [new_handler] + H[active + 1 :]
        return CESKState(
            C=C, E=handler_ctx.saved_env, S=S, K=restored_k, H=new_H, active_handler=-1
        )

    # ========================================
    # Effect handling
    # ========================================

    if isinstance(C, EffectControl):
        effect = C.effect

        # PythonAsyncSyntaxEscape - wrap and return to async_run
        # KEY FIX: Don't capture H, only capture K
        if isinstance(effect, PythonAsyncSyntaxEscape):
            original_action = effect.action
            captured_E, captured_S, captured_K = E, S, list(K)
            captured_active = active

            async def wrapped_action():
                result = original_action()
                import asyncio

                if asyncio.iscoroutine(result):
                    value = await result
                else:
                    value = result
                # Return state WITHOUT H - caller will provide current H
                return CESKState(
                    C=Value(value),
                    E=captured_E,
                    S=captured_S,
                    K=captured_K,
                    active_handler=captured_active,
                )

            return PythonAsyncSyntaxEscape(action=wrapped_action)

        # WithHandler - push new handler onto H
        if isinstance(effect, WithHandler):
            handles = get_handler_handles(effect.handler)
            if not handles:
                handles = frozenset({EffectBase})
            new_handler = HandlerCtx(
                handler=effect.handler,
                handles=handles,
                saved_env=E,
            )
            inner = effect.program
            if isinstance(inner, EffectBase):
                inner_control = EffectControl(inner)
            else:
                inner_control = ProgramControl(inner)
            return CESKState(
                C=inner_control,
                E=E,
                S=S,
                K=K,
                H=[new_handler] + H,
                active_handler=active,
            )

        # PureEffect shortcut
        from doeff.effects.pure import PureEffect

        if isinstance(effect, PureEffect):
            return CESKState(C=Value(effect.value), E=E, S=S, K=K, H=H, active_handler=active)

        # Find handler in H
        search_start = active + 1 if active >= 0 else 0
        match_result = _find_handler(type(effect), H, search_start)

        if match_result is None:
            from doeff.cesk_traceback import capture_traceback_safe

            unhandled_ex = UnhandledEffectError(f"No handler for {type(effect).__name__}")
            captured = capture_traceback_safe(K, unhandled_ex)
            return CESKState(
                C=Error(unhandled_ex, captured_traceback=captured),
                E=E,
                S=S,
                K=K,
                H=H,
                active_handler=active,
            )

        handler_idx, handler_ctx = match_result

        # Capture K into handler
        new_handler = replace(handler_ctx, captured_k=list(K))
        new_H = H[:handler_idx] + [new_handler] + H[handler_idx + 1 :]

        ctx = HandlerContext(
            store=S,
            env=E,
            delimited_k=list(K),
            handler_depth=handler_idx,
            outer_k=[],
            inherited_handlers=[],
            h=new_H,
        )

        # Invoke handler
        handler_program = handler_ctx.handler(effect, ctx)

        return CESKState(
            C=ProgramControl(handler_program),
            E=handler_ctx.saved_env,
            S=S,
            K=[],  # Handler runs with empty K
            H=new_H,
            active_handler=handler_idx,
        )

    # ========================================
    # Program execution
    # ========================================

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
                    H=H,
                    active_handler=active,
                )

            kleisli_fn_name: str | None = None
            kleisli_filename: str | None = None
            kleisli_lineno: int | None = None
            if program_call is not None:
                kleisli_source = getattr(program_call, "kleisli_source", None)
                original_func = (
                    getattr(kleisli_source, "original_func", None) if kleisli_source else None
                )
                if original_func is not None and hasattr(original_func, "__code__"):
                    code = original_func.__code__
                    kleisli_fn_name = program_call.function_name
                    kleisli_filename = code.co_filename
                    kleisli_lineno = code.co_firstlineno

            return CESKState(
                C=control,
                E=E,
                S=S,
                K=[
                    ReturnFrame(
                        gen,
                        E,
                        program_call=program_call,
                        kleisli_function_name=kleisli_fn_name,
                        kleisli_filename=kleisli_filename,
                        kleisli_lineno=kleisli_lineno,
                    )
                ]
                + K,
                H=H,
                active_handler=active,
            )
        except StopIteration as e:
            return CESKState(C=Value(e.value), E=E, S=S, K=K, H=H, active_handler=active)
        except Exception as ex:
            captured = capture_traceback_safe(K, ex, pre_captured=pre_captured)
            return CESKState(
                C=Error(ex, captured_traceback=captured), E=E, S=S, K=K, H=H, active_handler=active
            )

    # ========================================
    # Value with non-empty K - send to continuation
    # ========================================

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
                        H=H,
                        active_handler=active,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[
                        ReturnFrame(
                            frame.generator,
                            frame.saved_env,
                            program_call=frame.program_call,
                            kleisli_function_name=frame.kleisli_function_name,
                            kleisli_filename=frame.kleisli_filename,
                            kleisli_lineno=frame.kleisli_lineno,
                        )
                    ]
                    + K_rest,
                    H=H,
                    active_handler=active,
                )
            except StopIteration as e:
                return CESKState(
                    C=Value(e.value), E=frame.saved_env, S=S, K=K_rest, H=H, active_handler=active
                )
            except Exception as ex:
                captured = capture_traceback_safe(K_rest, ex, pre_captured=pre_captured)
                return CESKState(
                    C=Error(ex, captured_traceback=captured),
                    E=frame.saved_env,
                    S=S,
                    K=K_rest,
                    H=H,
                    active_handler=active,
                )

        # Legacy frame types (for backward compatibility during migration)
        from doeff.cesk.handler_frame import HandlerFrame, HandlerResultFrame
        from doeff.cesk.frames import Frame

        if isinstance(frame, (HandlerFrame, HandlerResultFrame, Frame)):
            result = frame.on_value(C.v, E, S, K_rest)
            # Preserve H and active_handler
            return CESKState(
                C=result.C, E=result.E, S=result.S, K=result.K, H=H, active_handler=active
            )

    # ========================================
    # Error with non-empty K - propagate through continuation
    # ========================================

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
                        H=H,
                        active_handler=active,
                    )

                return CESKState(
                    C=control,
                    E=frame.saved_env,
                    S=S,
                    K=[
                        ReturnFrame(
                            frame.generator,
                            frame.saved_env,
                            program_call=frame.program_call,
                            kleisli_function_name=frame.kleisli_function_name,
                            kleisli_filename=frame.kleisli_filename,
                            kleisli_lineno=frame.kleisli_lineno,
                        )
                    ]
                    + K_rest,
                    H=H,
                    active_handler=active,
                )
            except StopIteration as e:
                return CESKState(
                    C=Value(e.value), E=frame.saved_env, S=S, K=K_rest, H=H, active_handler=active
                )
            except Exception as propagated:
                captured = capture_traceback_safe(K_rest, propagated, pre_captured=pre_captured)
                return CESKState(
                    C=Error(propagated, captured_traceback=captured),
                    E=frame.saved_env,
                    S=S,
                    K=K_rest,
                    H=H,
                    active_handler=active,
                )

        # Legacy frame types
        from doeff.cesk.handler_frame import HandlerFrame, HandlerResultFrame
        from doeff.cesk.frames import Frame

        if isinstance(frame, (HandlerFrame, HandlerResultFrame, Frame)):
            result = frame.on_error(C.ex, E, S, K_rest)
            return CESKState(
                C=result.C, E=result.E, S=result.S, K=result.K, H=H, active_handler=active
            )

    head_desc = type(K[0]).__name__ if K else "empty"
    raise InterpreterInvariantError(
        f"Unhandled state: C={type(C).__name__}, K head={head_desc}, active={active}"
    )


__all__ = ["step"]
