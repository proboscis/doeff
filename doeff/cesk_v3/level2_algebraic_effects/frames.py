from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from doeff.program import Program

Handler = Callable[["EffectBase"], "Program[Any]"]


class EffectBase:
    pass


@dataclass(frozen=True)
class WithHandlerFrame:
    handler: Handler


@dataclass(frozen=True)
class DispatchingFrame:
    effect: EffectBase
    handler_idx: int
    handlers: tuple[Handler, ...]
    handler_started: bool = False

    def with_handler_started(self) -> DispatchingFrame:
        return replace(self, handler_started=True)
