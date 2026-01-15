"""Time effect handlers for Delay, WaitUntil, and GetTime."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from doeff.runtime import DelayPayload, HandlerResult, Resume, Schedule, WaitUntilPayload

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


def handle_get_time(
    effect: EffectBase,
    env: Environment,
    store: Store,
) -> Resume:
    current_time = store.get("__current_time__")
    if current_time is None:
        current_time = datetime.now()
    return Resume(current_time, store)


__all__ = [
    "handle_delay",
    "handle_wait_until",
    "handle_get_time",
]
