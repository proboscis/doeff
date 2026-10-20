"""Shared handler utilities — used by scheduler, try_handler, and any handler
that needs to capture and reinstall inner handlers from a continuation.
"""

from collections.abc import Callable, Generator
from typing import Any, Literal, TypeVar, cast

from doeff_vm import K, WithHandler

from doeff.do import do
from doeff.program import GetBoundaries, GetHandlers

_Handler = TypeVar("_Handler")


def reinstaller(captured: Callable[..., object]) -> Callable[[object], object]:
    """The installer that puts a captured handler back around a program — for an entry of
    ``GetHandlers(k)``, not for making a new handler.

    The VM holds the raw dispatcher of every installed handler: the ``@do`` function a
    ``@handler`` was made from, the clause data of a ``defhandler``, or a handler object of the
    Rust scheduler (``SchedulerPrompt``). Those were judged when they were first installed, so a
    handler that captures its inner handlers (``try_handler``, ``local_handler``, Spawn,
    doeff-traverse) reinstalls them with this, on the VM node itself. ``handler()`` is the door
    for making a handler and takes only a ``@do`` function (ADR-DOE-CORE-003); it would refuse a
    Rust handler object here. An entry that already carries the installer marker is called with
    the program, as ``with_handlers`` calls it.
    """
    if getattr(captured, "_doeff_is_handler_fn", False) is True:
        return cast(Callable[[object], object], captured)  # 印を確かめた installer は program だけを取る

    def install(body: object) -> object:
        return WithHandler(captured, body)

    return install


def inner_of(handlers: list[_Handler]) -> list[_Handler]:
    """The inner handlers in what ``GetHandlers(k)`` answered: every entry but the
    last, which is the handler that caught the effect (the caller itself).

    The one place of this rule. A handler that reads the stack only when it needs
    it (``doeff_traverse.handlers.sequential`` — once per Traverse, when the first
    item runs) yields ``GetHandlers(k)`` itself and passes the answer here, without
    the ``@do`` call of ``get_inner_handlers`` (agora-redesign #2958).
    """
    return handlers[:-1] if handlers else []


@do
def get_inner_handlers(
    k: K,
) -> Generator[GetHandlers, Any, list[Callable[..., object]]]:
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
            prog = reinstaller(h)(prog)
    """
    return inner_of((yield GetHandlers(k)))


@do
def get_inner_boundaries(
    k: K,
) -> Generator[
    GetBoundaries, Any, list[tuple[Literal["handler", "observer"], Callable[..., object]]]
]:
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
