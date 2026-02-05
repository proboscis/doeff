from __future__ import annotations

from typing import TYPE_CHECKING

from doeff.cesk_v3.errors import UnhandledEffectError
from doeff.cesk_v3.level1_cesk.state import CESKState, Value
from doeff.cesk_v3.level2_algebraic_effects.frames import (
    DispatchingFrame,
    EffectBase,
    Handler,
    WithHandlerFrame,
)

if TYPE_CHECKING:
    from doeff.cesk_v3.level1_cesk.state import Kontinuation


def collect_available_handlers(K: Kontinuation) -> list[Handler]:
    """Collect handlers available for a new effect dispatch.

    Convention: handlers[0] = innermost, handlers[N-1] = outermost.
    When inside a DispatchingFrame, only outer handlers (after current idx) are available.
    """
    handlers: list[Handler] = []

    for frame in K:
        if isinstance(frame, WithHandlerFrame):
            handlers.append(frame.handler)
        elif isinstance(frame, DispatchingFrame):
            outer_handlers = list(frame.handlers[frame.handler_idx + 1 :])
            return outer_handlers + handlers

    return handlers


def start_dispatch(effect: EffectBase, state: CESKState) -> CESKState:
    C, E, S, K = state.C, state.E, state.S, state.K

    handlers = collect_available_handlers(K)

    if not handlers:
        raise UnhandledEffectError(effect)

    df = DispatchingFrame(
        effect=effect,
        handler_idx=0,
        handlers=tuple(handlers),
        handler_started=False,
    )

    return CESKState(C=Value(None), E=E, S=S, K=[df] + K)
