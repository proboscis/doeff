"""Shared type aliases for effect functions that accept programs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeAlias

    from doeff.program import Program
    from doeff.types import Effect

    ProgramLike: TypeAlias = Program[object] | Effect
else:  # pragma: no cover - runtime fallback for type-only alias
    ProgramLike = object  # type: ignore[assignment]


__all__ = ["ProgramLike"]
