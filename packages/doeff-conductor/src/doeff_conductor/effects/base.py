"""
Base effect class for doeff-conductor effects.

All conductor effects inherit from ConductorEffectBase and are compatible
with doeff's CESK interpreter through the Effect protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generator, TypeVar


E = TypeVar("E", bound="ConductorEffectBase")


@dataclass(frozen=True, kw_only=True)
class ConductorEffectBase:
    """Base class for conductor effects.

    All conductor effects inherit from this class and are compatible with
    doeff's CESK interpreter through the Effect protocol.
    """

    created_at: Any = field(default=None, compare=False)  # EffectCreationContext | None

    def with_created_at(self: E, created_at: Any) -> E:  # EffectCreationContext | None
        """Return a copy with updated creation context."""
        if created_at is self.created_at:
            return self
        return replace(self, created_at=created_at)

    def intercept(self: E, transform: Callable[[Any], Any]) -> E:
        """Return a copy where any nested programs are intercepted.

        Conductor effects don't contain nested programs by default,
        so this returns self unchanged.
        Required for CESK interpreter compatibility.
        """
        return self

    def to_generator(self) -> Generator[Any, Any, Any]:
        """An Effect is a single-step program that yields itself."""
        result = yield self
        return result


__all__ = ["ConductorEffectBase"]
