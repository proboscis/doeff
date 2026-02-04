from __future__ import annotations

import warnings
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
    Frame,
)
from doeff.cesk_v3.level1_cesk.step import cesk_step
from doeff.cesk_v3.level2_algebraic_effects.state import get_ae_state, set_ae_state
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    ControlPrimitive,
    WithHandler,
    Resume,
    Abort,
)


def _close_generators_in_k(k: tuple[Frame, ...]) -> None:
    for frame in k:
        if isinstance(frame, ReturnFrame):
            try:
                frame.generator.close()
            except Exception:
                pass


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

    if isinstance(primitive, Resume):
        ae = get_ae_state(S)
        handler_idx = ae.active_handler_index

        if handler_idx < 0:
            raise RuntimeError("Resume called with no active handler")

        captured_k, k_id = ae.get_captured_at(handler_idx)

        if captured_k is None or k_id is None:
            raise RuntimeError("Resume called but no continuation was captured")

        if ae.is_consumed(k_id):
            raise RuntimeError(
                f"One-shot violation: continuation {k_id} has already been consumed. "
                "Continuations can only be resumed once."
            )

        new_ae = ae.clear_captured_at(handler_idx).mark_consumed(k_id)
        new_k = list(captured_k) + K

        return CESKState(
            C=Value(primitive.value),
            E=E,
            S=set_ae_state(S, new_ae),
            K=new_k,
        )

    if isinstance(primitive, Abort):
        ae = get_ae_state(S)
        handler_idx = ae.active_handler_index

        if handler_idx >= 0:
            captured_k, k_id = ae.get_captured_at(handler_idx)

            if captured_k is not None:
                warnings.warn(
                    f"Abort: abandoning captured continuation (k_id={k_id}). "
                    "Closing generators in abandoned continuation.",
                    stacklevel=2,
                )
                _close_generators_in_k(captured_k)

            new_ae = ae.clear_captured_at(handler_idx)
            if k_id is not None:
                new_ae = new_ae.mark_consumed(k_id)
        else:
            new_ae = ae

        return CESKState(
            C=Value(primitive.value),
            E=E,
            S=set_ae_state(S, new_ae),
            K=K,
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
