"""Pure effect handlers.

Direct ScheduledEffectHandler implementation for PureEffect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.runtime import HandlerResult, Resume

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_pure_effect(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    return Resume(effect.value, store)


__all__ = [
    "handle_pure_effect",
]
