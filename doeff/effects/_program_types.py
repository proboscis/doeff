"""Shared type aliases for effect functions that accept programs."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeAlias

    from doeff.program import Program

    ProgramLike: TypeAlias = Program[object] | Callable[[], Program[object]]
else:  # pragma: no cover - runtime fallback for type-only alias
    ProgramLike = object  # type: ignore[assignment]


__all__ = ["ProgramLike"]

