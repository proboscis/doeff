from __future__ import annotations

from typing import Any

from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.state import CESKState, ProgramControl, Value
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    WithHandlerFrame,
)
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume, WithHandler


def handle_with_handler(wh: WithHandler[Any], state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    return CESKState(
        C=ProgramControl(wh.program),
        E=E,
        S=S,
        K=[WithHandlerFrame(handler=wh.handler)] + K,
    )


def handle_resume(resume: Resume, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    if len(K) < 2:
        raise RuntimeError("Resume without proper K structure")

    handler_frame = K[0]
    if not isinstance(handler_frame, ReturnFrame):
        raise RuntimeError(f"Expected handler ReturnFrame, got {type(handler_frame)}")

    df_idx = None
    for i, frame in enumerate(K[1:], start=1):
        if isinstance(frame, DispatchingFrame):
            df_idx = i
            break

    if df_idx is None:
        raise RuntimeError("Resume without DispatchingFrame")

    df = K[df_idx]
    assert isinstance(df, DispatchingFrame)
    handler_gen = K[0]
    user_continuation = K[df_idx + 1 :]
    target_handler = df.handlers[df.handler_idx]

    whf_idx = None
    for i, frame in enumerate(user_continuation):
        if isinstance(frame, WithHandlerFrame) and frame.handler is target_handler:
            whf_idx = i
            break

    if whf_idx is None:
        raise RuntimeError("Resume: cannot find handler's WithHandlerFrame")

    new_k = (
        list(user_continuation[:whf_idx])
        + [handler_gen]
        + list(user_continuation[whf_idx:])
    )

    return CESKState(C=Value(resume.value), E=E, S=S, K=new_k)


def handle_forward(forward: Forward, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    df_idx = None
    for i, frame in enumerate(K):
        if isinstance(frame, DispatchingFrame):
            df_idx = i
            break

    if df_idx is None:
        raise RuntimeError("Forward called outside of handler context")

    current_df = K[df_idx]
    assert isinstance(current_df, DispatchingFrame)
    outer_handlers = current_df.handlers[: current_df.handler_idx]

    if not outer_handlers:
        raise UnhandledEffectError(forward.effect)

    new_df = DispatchingFrame(
        effect=forward.effect,
        handler_idx=len(outer_handlers) - 1,
        handlers=outer_handlers,
        handler_started=False,
    )

    return CESKState(C=Value(None), E=E, S=S, K=[new_df] + K)


def handle_implicit_abandonment(handler_result: Any, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    if not isinstance(K[0], DispatchingFrame):
        raise RuntimeError("Implicit abandonment without DispatchingFrame at K[0]")

    df = K[0]
    user_continuation = K[1:]
    target_handler = df.handlers[df.handler_idx]

    whf_idx = None
    for i, frame in enumerate(user_continuation):
        if isinstance(frame, WithHandlerFrame) and frame.handler is target_handler:
            whf_idx = i
            break

    if whf_idx is None:
        raise RuntimeError(
            "Implicit abandonment: cannot find handler's WithHandlerFrame. "
            "This is a VM invariant violation."
        )

    for frame in user_continuation[:whf_idx]:
        if isinstance(frame, ReturnFrame):
            try:
                frame.generator.close()
            except Exception:
                pass

    new_k = list(user_continuation[whf_idx + 1 :])

    return CESKState(C=Value(handler_result), E=E, S=S, K=new_k)
