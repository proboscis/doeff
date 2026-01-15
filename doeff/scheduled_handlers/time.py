"""Time effect handlers for Delay and WaitUntil effects."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from doeff.runtime import DelayPayload, HandlerResult, Schedule, WaitUntilPayload

if TYPE_CHECKING:
    from doeff._types_internal import EffectBase
    from doeff.cesk import Environment, Store


def handle_delay(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    return Schedule(DelayPayload(timedelta(seconds=effect.seconds)), store)


def handle_wait_until(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> HandlerResult:
    return Schedule(WaitUntilPayload(effect.target_time), store)


__all__ = [
    "handle_delay",
    "handle_wait_until",
]
