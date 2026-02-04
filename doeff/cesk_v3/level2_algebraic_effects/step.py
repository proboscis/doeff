from __future__ import annotations

from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    Done,
    EffectYield,
    Error,
    Failed,
    ProgramControl,
    Value,
)
from doeff.cesk_v3.level1_cesk.step import cesk_step
from doeff.cesk_v3.level2_algebraic_effects.dispatch import start_dispatch
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    EffectBase,
    WithHandlerFrame,
)
from doeff.cesk_v3.level2_algebraic_effects.handlers import (
    handle_forward,
    handle_get_continuation,
    handle_implicit_abandonment,
    handle_resume,
    handle_resume_continuation,
    handle_with_handler,
)
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    ControlPrimitive,
    Forward,
    GetContinuation,
    Resume,
    ResumeContinuation,
    WithHandler,
)
from doeff.program import ProgramBase


def level2_step(state: CESKState) -> CESKState | Done | Failed:
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, Value) and K and isinstance(K[0], WithHandlerFrame):
        return CESKState(C=C, E=E, S=S, K=K[1:])

    if isinstance(C, Error) and K and isinstance(K[0], WithHandlerFrame):
        return CESKState(C=C, E=E, S=S, K=K[1:])

    if isinstance(C, Value) and K and isinstance(K[0], DispatchingFrame):
        df = K[0]

        if not df.handler_started:
            if df.handler_idx < 0:
                raise UnhandledEffectError(df.effect)

            handler = df.handlers[df.handler_idx]
            handler_program = handler(df.effect)
            new_df = df.with_handler_started()

            return CESKState(
                C=ProgramControl(handler_program),
                E=E,
                S=S,
                K=[new_df] + K[1:],
            )
        else:
            return handle_implicit_abandonment(C.value, state)

    if isinstance(C, EffectYield):
        yielded = C.yielded

        if isinstance(yielded, WithHandler):
            return handle_with_handler(yielded, state)

        if isinstance(yielded, Resume):
            return handle_resume(yielded, state)

        if isinstance(yielded, Forward):
            return handle_forward(yielded, state)

        if isinstance(yielded, GetContinuation):
            return handle_get_continuation(yielded, state)

        if isinstance(yielded, ResumeContinuation):
            return handle_resume_continuation(yielded, state)

        if isinstance(yielded, ProgramBase):
            return CESKState(C=ProgramControl(yielded), E=E, S=S, K=K)

        if isinstance(yielded, EffectBase):
            return start_dispatch(yielded, state)

        raise TypeError(f"Unknown yield type: {type(yielded)}")

    return cesk_step(state)
