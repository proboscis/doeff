from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from doeff.program import Program

Handler = Callable[["EffectBase"], "Program[Any]"]

_frame_id_counter = itertools.count(1)


def _next_frame_id() -> int:
    return next(_frame_id_counter)


class EffectBase:
    pass


@dataclass(frozen=True)
class WithHandlerFrame:
    handler: Handler
    frame_id: int = field(default_factory=_next_frame_id, compare=False)


@dataclass(frozen=True)
class DispatchingFrame:
    """Tracks effect dispatch progress.

    Attributes:
        effect: The effect being dispatched
        handler_idx: Current handler index (0 = innermost)
        handlers: Snapshot of available handlers at dispatch start
        handler_started: Whether the handler generator has been started
        forwarded: Whether this handler has called Forward. If True, Resume
                   is not allowed (user was already resumed by outer handler).
        frame_id: Unique identifier for debugging
    """

    effect: EffectBase
    handler_idx: int
    handlers: tuple[Handler, ...]
    handler_started: bool = False
    forwarded: bool = False
    frame_id: int = field(default_factory=_next_frame_id, compare=False)

    def with_handler_started(self) -> DispatchingFrame:
        return replace(self, handler_started=True)

    def with_forwarded(self) -> DispatchingFrame:
        """Mark this dispatch as having forwarded. Resume will be disallowed."""
        return replace(self, forwarded=True)
