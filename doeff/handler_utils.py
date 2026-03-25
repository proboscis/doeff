"""Shared handler utilities — used by scheduler, try_handler, and any handler
that needs to capture and reinstall inner handlers from a continuation.
"""

from doeff.do import do
from doeff.program import GetHandlers


@do
def get_inner_handlers(k):
    """Get inner handler callables from a continuation, excluding the caller.

    Yields GetHandlers(k) and drops the last entry (the handler that caught
    the effect — i.e., the caller). Returns the remaining inner handlers
    (innermost first).

    Usage in a handler:
        inner_hs = yield get_inner_handlers(k)
        for h in inner_hs:
            prog = WithHandler(h, prog)
    """
    all_hs = yield GetHandlers(k)
    if all_hs:
        return all_hs[:-1]
    return []
