"""Reader effect handlers.

Direct ScheduledEffectHandler implementation for AskEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store
    from doeff.runtime import Continuation, Scheduler


def handle_ask(
    effect: EffectBase,
    env: Environment,
    store: Store,
    k: Continuation,
    scheduler: Scheduler | None,
) -> HandlerResult:
    """Handle AskEffect - retrieves value from environment by key."""
    if effect.key not in env:
        raise KeyError(f"Missing environment key: {effect.key!r}")
    value = env[effect.key]
    return Resume(value, store)


__all__ = [
    "handle_ask",
]
