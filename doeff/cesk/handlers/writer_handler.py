"""Writer effect handler for Tell effect.

Note: Listen effect is handled by core_handler because it nests a sub-program
and requires access to the full continuation without forwarding pollution.
"""

from __future__ import annotations

from doeff.do import do
from doeff._types_internal import EffectBase
from doeff.cesk.handler_frame import HandlerContext
from doeff.cesk.state import CESKState
from doeff.effects.writer import WriterTellEffect


@do
def writer_handler(effect: EffectBase, ctx: HandlerContext):
    store = dict(ctx.store)

    if isinstance(effect, WriterTellEffect):
        log = list(store.get("__log__", []))
        message = effect.message
        if isinstance(message, (list, tuple)):
            log.extend(message)
        else:
            log.append(message)
        new_store = {**store, "__log__": log}
        return CESKState.with_value(None, ctx.env, new_store, ctx.k)

    result = yield effect
    return result


__all__ = ["writer_handler"]
