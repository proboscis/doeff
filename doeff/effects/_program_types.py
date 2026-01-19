"""Shared type aliases for effect functions that accept programs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import TypeAlias

    from doeff.program import ProgramBase
    from doeff.types import Effect

    # Use ProgramBase[Any] to accept any Program/KleisliProgramCall regardless of
    # result type. ProgramBase is invariant in T, so ProgramBase[object] would
    # reject ProgramBase[str] etc. Using Any avoids this variance issue.
    #
    # Effect is included because while EffectBase extends ProgramBase, the Effect
    # protocol is used in some type annotations. At runtime, effects are validated
    # via isinstance checks against EffectBase (which is a ProgramBase subclass).
    ProgramLike: TypeAlias = ProgramBase[Any] | Effect
else:  # pragma: no cover - runtime fallback for type-only alias
    ProgramLike = object  # type: ignore[assignment]


__all__ = ["ProgramLike"]
