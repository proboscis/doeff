"""Shared handler utilities — used by scheduler, try_handler, and any handler
that needs to capture and reinstall inner handlers from a continuation.
"""

from doeff.do import do
from doeff.program import GetBoundaries, GetHandlers


@do
def get_inner_handlers(k):
    """Get inner handler callables from a continuation, excluding the caller.

    Yields GetHandlers(k) and drops the last entry (the handler that caught
    the effect — i.e., the caller). Returns the remaining inner handlers
    (innermost first).

    NOTE: this captures prompt (WithHandler) boundaries only — WithObserve
    observer boundaries are NOT included. Use get_inner_boundaries when the
    reinstalled program must keep observers attached (e.g. Spawn).

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
def get_inner_boundaries(k):
    """Get the interleaved boundary stack from a continuation, excluding the caller.

    Yields GetBoundaries(k) — every handler (WithHandler) and observer
    (WithObserve) boundary between the yield site and the handler that caught
    the effect, innermost first — and drops the last entry (the catching
    handler itself, i.e. the caller). Returns a list of
    ("handler" | "observer", callable) tuples.

    Raises RuntimeError if the captured chain does not terminate at a handler
    boundary — the catching handler must always be the outermost entry.

    Usage in a handler (reinstall preserving nesting order):
        boundaries = yield get_inner_boundaries(k)
        for kind, boundary_callable in boundaries:
            if kind == "handler":
                prog = handler(boundary_callable)(prog)
            else:
                prog = WithObserve(boundary_callable, prog)
    """
    all_boundaries = yield GetBoundaries(k)
    if not all_boundaries:
        return []
    last_kind, _last_callable = all_boundaries[-1]
    if last_kind != "handler":
        raise RuntimeError(
            "get_inner_boundaries: expected the catching handler as the last "
            f"chain entry, got kind={last_kind!r}"
        )
    return [(kind, boundary_callable) for kind, boundary_callable in all_boundaries[:-1]]
