"""
Base effect class for doeff-conductor effects.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from doeff import Effect, EffectBase, Program

E = TypeVar("E", bound="ConductorEffectBase")
T = TypeVar("T")


@dataclass(frozen=True, kw_only=True)
class ConductorEffectBase(EffectBase[T]):
    """Base class for conductor effects.

    Inherits from doeff's EffectBase for effect interpreter compatibility. ``T`` is the effect's
    answer: a subclass writes ``ConductorEffectBase[<answer>]`` so the answer is read from the
    declaration (ADR-DOE-API-001 R2).
    """

    def intercept(
        self: E,
        transform: Callable[[Effect], Effect | Program],
    ) -> E:
        """Conductor effects have no nested programs, returns self unchanged."""
        return self
