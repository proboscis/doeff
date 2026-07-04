"""Shared handler utilities — used by scheduler, try_handler, and any handler
that needs to capture and reinstall inner handlers from a continuation.
"""

from doeff.do import do
from doeff.program import GetHandlers, GetObservers


@do
def get_inner_handlers(k):
    """Get inner handler callables from a continuation, excluding the caller.

    Yields GetHandlers(k) and drops the last entry (the handler that caught
    the effect — i.e., the caller). Returns the remaining inner handlers
    (innermost first).

    Usage in a handler:
        inner_hs = yield get_inner_handlers(k)
        for h in inner_hs:
            prog = handler(h)(prog)
    """
    all_hs = yield GetHandlers(k)
    if all_hs:
        return all_hs[:-1]
    return []


@do
def get_inner_observers(k):
    """Get inner observer callables from a continuation.

    Yields GetObservers(k) and returns the observer callables of every
    WithObserve boundary between the perform site and the handler that
    caught the effect (innermost first). Unlike get_inner_handlers there
    is no caller entry to drop: the catching handler is a prompt boundary,
    never an intercept boundary.

    Usage in a handler:
        inner_obs = yield get_inner_observers(k)
        for obs in inner_obs:
            prog = WithObserve(obs, prog)
    """
    observers = yield GetObservers(k)
    return list(observers)
