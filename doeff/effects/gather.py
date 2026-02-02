from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import Effect, EffectBase, create_effect_with_trace
from .spawn import Waitable

if TYPE_CHECKING:
    from doeff.program import ProgramBase


@dataclass(frozen=True)
class GatherEffect(EffectBase):
    """Effect to gather results from multiple items (Programs or Waitables).

    The `items` field accepts:
    - ProgramBase: Executed sequentially in sync runtime, concurrently in async
    - Waitable (Task/Future): Waited on concurrently
    """
    items: tuple[Any, ...]  # Programs or Waitables
    _partial_results: tuple[Any, ...] | None = field(default=None, compare=False)

    # Backwards compatibility alias
    @property
    def futures(self) -> tuple[Any, ...]:
        return self.items


def _validate_gather_items(items: tuple[Any, ...]) -> tuple[Any, ...]:
    """Validate that items are either Waitable or ProgramBase."""
    from doeff.program import ProgramBase

    for i, item in enumerate(items):
        if not isinstance(item, (Waitable, ProgramBase)):
            raise TypeError(
                f"Gather expects Waitable or Program, got {type(item).__name__} at index {i}."
            )
    return items


def gather(*items: "Waitable[Any] | ProgramBase[Any]") -> GatherEffect:
    validated = _validate_gather_items(tuple(items))
    return create_effect_with_trace(GatherEffect(items=validated))


def Gather(*items: "Waitable[Any] | ProgramBase[Any]") -> Effect:
    validated = _validate_gather_items(tuple(items))
    return create_effect_with_trace(
        GatherEffect(items=validated), skip_frames=3
    )


__all__ = [
    "Gather",
    "GatherEffect",
    "gather",
]
