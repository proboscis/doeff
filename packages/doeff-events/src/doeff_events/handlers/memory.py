"""In-memory publish/subscribe handler."""

from __future__ import annotations

from typing import Any

from doeff import Pass, Resume
from doeff.effects import CreateExternalPromise, ExternalPromise, Wait
from doeff_events.effects import PublishEffect, WaitForEventEffect

ListenerMap = dict[type[Any], list[ExternalPromise[Any]]]
BufferedEvents = list[Any]
_NO_EVENT = object()


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
) -> None:
    if not promise_ids:
        return

    for event_type, queued in list(listeners.items()):
        remaining = [promise for promise in queued if id(promise) not in promise_ids]
        if remaining:
            listeners[event_type] = remaining
        else:
            listeners.pop(event_type, None)


def _pop_buffered_event(
    buffered_events: BufferedEvents,
    event_types: tuple[type[Any], ...],
) -> Any:
    for idx, event in enumerate(buffered_events):
        if isinstance(event, event_types):
            return buffered_events.pop(idx)
    return _NO_EVENT


def event_handler():
    """Create a stateful in-memory pub/sub handler.

    WaitForEvent creates a Promise and blocks via Wait(promise.future).
    Publish resolves promises for listeners whose registered type matches
    via isinstance(event, registered_type).
    """

    listeners: ListenerMap = {}
    buffered_events: BufferedEvents = []

    def handler(effect: Any, k: Any):
        if isinstance(effect, WaitForEventEffect):
            buffered_event = _pop_buffered_event(buffered_events, effect.event_types)
            if buffered_event is not _NO_EVENT:
                return (yield Resume(k, buffered_event))

            promise = yield CreateExternalPromise()

            for event_type in effect.event_types:
                listeners.setdefault(event_type, []).append(promise)

            try:
                event = yield Wait(promise.future)
            finally:
                _remove_promises(listeners, {id(promise)})

            return (yield Resume(k, event))

        if isinstance(effect, PublishEffect):
            event = effect.event
            matched = _matching_promises(listeners, event)

            if not matched:
                buffered_events.append(event)
                return (yield Resume(k, None))

            _remove_promises(listeners, set(matched))

            for promise in matched.values():
                promise.complete(event)

            return (yield Resume(k, None))

        yield Pass()

    return handler


__all__ = ["event_handler"]
