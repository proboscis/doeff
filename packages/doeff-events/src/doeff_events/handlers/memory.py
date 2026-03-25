"""In-memory publish/subscribe handler."""

from typing import Any

from doeff import Pass, Resume, do
from doeff_core_effects.scheduler import CreatePromise, CompletePromise, Wait
from doeff_events.effects import PublishEffect, WaitForEventEffect


def _matching_promises(listeners, event):
    matched = {}
    for event_type, queued in listeners.items():
        if not isinstance(event, event_type):
            continue
        for promise in queued:
            matched.setdefault(id(promise), promise)
    return matched


def _remove_promises(listeners, promise_ids):
    if not promise_ids:
        return
    for event_type, queued in list(listeners.items()):
        remaining = [p for p in queued if id(p) not in promise_ids]
        if remaining:
            listeners[event_type] = remaining
        else:
            listeners.pop(event_type, None)


def event_handler():
    """Create a stateful in-memory pub/sub handler.

    WaitForEvent creates a Promise and blocks via Wait(promise.future).
    Publish resolves promises for listeners whose registered type matches.
    """
    listeners: dict[type, list] = {}

    @do
    def handler(effect, k):
        if isinstance(effect, WaitForEventEffect):
            promise = yield CreatePromise()
            for event_type in effect.event_types:
                listeners.setdefault(event_type, []).append(promise)
            try:
                event = yield Wait(promise.future)
            finally:
                _remove_promises(listeners, {id(promise)})
            result = yield Resume(k, event)
            return result

        if isinstance(effect, PublishEffect):
            event = effect.event
            matched = _matching_promises(listeners, event)
            _remove_promises(listeners, set(matched))
            for promise in matched.values():
                yield CompletePromise(promise, event)
            result = yield Resume(k, None)
            return result

        yield Pass(effect, k)

    return handler


__all__ = ["event_handler"]
