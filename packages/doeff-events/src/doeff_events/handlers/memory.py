"""In-memory publish/subscribe handler."""

from __future__ import annotations

from typing import Any

from doeff import Delegate, Resume
from doeff.effects import CreateExternalPromise, ExternalPromise, Wait
from doeff_events.effects import PublishEffect, WaitForEventEffect

ListenerMap = dict[type[Any], list[ExternalPromise[Any]]]


def _matching_promises(
    listeners: ListenerMap,
    event: Any,
) -> dict[int, ExternalPromise[Any]]:
    matched: dict[int, ExternalPromise[Any]] = {}
    for event_type, queued in listeners.items():
        if not isinstance(event, event_type):
            continue
        for promise in queued:
            matched.setdefault(id(promise), promise)
    return matched


def _remove_promises(
    listeners: ListenerMap,
    promise_ids: set[int],
) -> ListenerMap:
    if not promise_ids:
        return listeners

    updated: ListenerMap = {}
    for event_type, queued in list(listeners.items()):
        remaining = [promise for promise in queued if id(promise) not in promise_ids]
        if remaining:
            updated[event_type] = remaining
    return updated


def event_handler():
    """Create a stateful in-memory pub/sub handler.

    WaitForEvent creates an ExternalPromise and blocks via Wait(promise.future).
    Publish resolves promises for listeners whose registered type matches
    via isinstance(event, registered_type).
    """

    listeners: ListenerMap = {}

    def handler(effect: Any, k: Any):
        nonlocal listeners

        if isinstance(effect, WaitForEventEffect):
            promise = yield CreateExternalPromise()

            for event_type in effect.event_types:
                listeners.setdefault(event_type, []).append(promise)

            try:
                event = yield Wait(promise.future)
            finally:
                listeners = _remove_promises(listeners, {id(promise)})

            return (yield Resume(k, event))

        if isinstance(effect, PublishEffect):
            event = effect.event
            matched = _matching_promises(listeners, event)
            listeners = _remove_promises(listeners, set(matched))

            for promise in matched.values():
                promise.complete(event)

            return (yield Resume(k, None))

        delegated = yield Delegate()
        return (yield Resume(k, delegated))

    return handler


__all__ = ["event_handler"]  # noqa: DOEFF021
