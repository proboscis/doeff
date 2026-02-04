from __future__ import annotations

import itertools
from typing import Any

from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.state import CESKState, ProgramControl, Value
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    WithHandlerFrame,
)
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

CONSUMED_CONTINUATIONS_KEY = "_cesk_consumed_continuations"
_continuation_id_counter = itertools.count(1)


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


def handle_get_continuation(gc: GetContinuation, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    if len(K) < 2:
        raise RuntimeError("GetContinuation without proper K structure")

    handler_frame = K[0]
    if not isinstance(handler_frame, ReturnFrame):
        raise RuntimeError(f"Expected handler ReturnFrame, got {type(handler_frame)}")

    df_idx = None
    for i, frame in enumerate(K[1:], start=1):
        if isinstance(frame, DispatchingFrame):
            df_idx = i
            break

    if df_idx is None:
        raise RuntimeError("GetContinuation without DispatchingFrame")

    df = K[df_idx]
    assert isinstance(df, DispatchingFrame)
    user_continuation = K[df_idx + 1 :]
    target_handler = df.handlers[df.handler_idx]

    whf_idx = None
    for i, frame in enumerate(user_continuation):
        if isinstance(frame, WithHandlerFrame) and frame.handler is target_handler:
            whf_idx = i
            break

    if whf_idx is None:
        raise RuntimeError("GetContinuation: cannot find handler's WithHandlerFrame")

    captured_frames = tuple(user_continuation[:whf_idx])
    cont_id = next(_continuation_id_counter)
    continuation = Continuation(cont_id=cont_id, frames=captured_frames)

    return CESKState(C=Value(continuation), E=E, S=S, K=K)


def handle_resume_continuation(rc: ResumeContinuation, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    consumed: set[int] = S.get(CONSUMED_CONTINUATIONS_KEY, set())
    if rc.continuation.cont_id in consumed:
        raise RuntimeError(
            f"Continuation {rc.continuation.cont_id} already consumed (one-shot violation)"
        )

    new_consumed = consumed | {rc.continuation.cont_id}
    new_store = {**S, CONSUMED_CONTINUATIONS_KEY: new_consumed}

    if len(K) < 2:
        raise RuntimeError("ResumeContinuation without proper K structure")

    handler_frame = K[0]
    if not isinstance(handler_frame, ReturnFrame):
        raise RuntimeError(f"Expected handler ReturnFrame, got {type(handler_frame)}")

    df_idx = None
    for i, frame in enumerate(K[1:], start=1):
        if isinstance(frame, DispatchingFrame):
            df_idx = i
            break

    if df_idx is None:
        raise RuntimeError("ResumeContinuation without DispatchingFrame")

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
        raise RuntimeError("ResumeContinuation: cannot find handler's WithHandlerFrame")

    if rc.continuation.started:
        new_k = (
            list(rc.continuation.frames)
            + [handler_gen]
            + list(user_continuation[whf_idx:])
        )
        return CESKState(C=Value(rc.value), E=E, S=new_store, K=new_k)
    else:
        if rc.continuation.program is None:
            raise RuntimeError("Unstarted continuation must have a program")

        handler_frames = [
            WithHandlerFrame(handler=h) for h in rc.continuation.handlers
        ]
        new_k = handler_frames + [handler_gen] + list(user_continuation[whf_idx:])

        return CESKState(
            C=ProgramControl(rc.continuation.program), E=E, S=new_store, K=new_k
        )


def handle_get_handlers(gh: GetHandlers, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    for frame in K:
        if isinstance(frame, DispatchingFrame):
            return CESKState(C=Value(frame.handlers), E=E, S=S, K=K)

    raise RuntimeError("GetHandlers called outside handler context")


def handle_create_continuation(cc: CreateContinuation, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    cont_id = next(_continuation_id_counter)
    continuation = Continuation(
        cont_id=cont_id,
        frames=(),
        program=cc.program,
        started=False,
        handlers=cc.handlers,
    )

    return CESKState(C=Value(continuation), E=E, S=S, K=K)
