"""Shared type aliases for effect functions that accept programs."""


from typing import Any, TypeAlias

from doeff.program import ProgramBase
from doeff.types import Effect

# Use ProgramBase[Any] to accept any Program regardless of
# result type. ProgramBase is invariant in T, so ProgramBase[object] would
# reject ProgramBase[str] etc. Using Any avoids this variance issue.
#
# Keep this as a real runtime union so @do auto-unwrap can inspect annotations
# like ``*programs: ProgramLike`` and preserve program/effect objects as data.
ProgramLike: TypeAlias = ProgramBase[Any] | Effect


__all__ = ["ProgramLike"]
