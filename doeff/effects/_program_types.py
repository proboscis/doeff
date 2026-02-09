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
    # Effect is included because effect values are accepted as data at API
    # boundaries and normalized via Perform(effect). EffectBase is not ProgramBase
    # under explicit EffectValue/DoExpr separation semantics.
    ProgramLike: TypeAlias = ProgramBase[Any] | Effect
else:  # pragma: no cover - runtime fallback for type-only alias
    ProgramLike = object  # type: ignore[assignment]


__all__ = ["ProgramLike"]
