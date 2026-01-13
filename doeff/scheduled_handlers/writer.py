"""Writer effect handlers.

Direct ScheduledEffectHandler implementation for WriterTellEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store
    from doeff.runtime import Continuation, Scheduler


def handle_writer_tell(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle WriterTellEffect - appends message to log."""
    log = store.get("__log__", [])
    new_log = log + [effect.message]
    new_store = {**store, "__log__": new_log}
    return Resume(None, new_store)


__all__ = [
    "handle_writer_tell",
]
