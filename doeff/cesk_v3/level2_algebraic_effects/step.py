from __future__ import annotations

from typing import Any

from doeff.cesk_v3.level1_cesk import (
    CESKState,
    Value,
    Error,
    EffectYield,
    Done,
    Failed,
    WithHandlerFrame,
    ProgramControl,
    ReturnFrame,
)
from doeff.cesk_v3.level1_cesk.step import cesk_step
from doeff.cesk_v3.level2_algebraic_effects.state import get_ae_state, set_ae_state
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    ControlPrimitive,
    WithHandler,
)


def translate_control_primitive(
    primitive: ControlPrimitive, state: CESKState
) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(primitive, WithHandler):
        ae = get_ae_state(S)
        new_ae = ae.push_handler(primitive.handler)
        new_s = set_ae_state(S, new_ae)
        return CESKState(
            C=ProgramControl(primitive.program),
            E=E,
            S=new_s,
            K=[WithHandlerFrame()] + K,
        )

    raise NotImplementedError(f"Control primitive not implemented: {type(primitive).__name__}")


def level2_step(state: CESKState) -> CESKState | Done | Failed:
    C, E, S, K = state.C, state.E, state.S, state.K

    if isinstance(C, Value) and K and isinstance(K[0], WithHandlerFrame):
        ae = get_ae_state(S)
        new_ae = ae.pop_handler()
        return CESKState(
            C=C,
            E=E,
            S=set_ae_state(S, new_ae),
            K=K[1:],
        )

    if isinstance(C, Error) and K and isinstance(K[0], WithHandlerFrame):
        ae = get_ae_state(S)
        new_ae = ae.pop_handler()
        return CESKState(
            C=C,
            E=E,
            S=set_ae_state(S, new_ae),
            K=K[1:],
        )

    result = cesk_step(state)

    if isinstance(result, CESKState) and isinstance(result.C, EffectYield):
        yielded = result.C.yielded
        if isinstance(yielded, ControlPrimitive):
            return translate_control_primitive(yielded, result)

    return result
