"""
Base effect class for doeff-conductor effects.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from doeff import Effect, EffectBase, Program

E = TypeVar("E", bound="ConductorEffectBase")


@dataclass(frozen=True, kw_only=True)
class ConductorEffectBase(EffectBase):
    """Base class for conductor effects.

    Inherits from doeff's EffectBase for effect interpreter compatibility.
    """

    def intercept(
        self: E,
        transform: Callable[[Effect], Effect | Program],
    ) -> E:
        """Conductor effects have no nested programs, returns self unchanged."""
        return self


__all__ = ["ConductorEffectBase"]
