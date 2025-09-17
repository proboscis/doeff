"""Gather effects for parallel programs."""

from __future__ import annotations

from collections.abc import Mapping

from ._program_types import ProgramLike
from .base import Effect, create_effect_with_trace


class gather:
    """Gather effects for parallel programs."""

    @staticmethod
    def gather(*programs: ProgramLike) -> Effect:
        """Gather results from multiple programs."""
        return create_effect_with_trace("gather.gather", programs)

    @staticmethod
    def gather_dict(programs: Mapping[str, ProgramLike]) -> Effect:
        """Gather results from a dict of programs."""
        return create_effect_with_trace("gather.gather_dict", programs)


# Uppercase aliases
def Gather(*programs: ProgramLike) -> Effect:
    """Gather: Gather results from multiple programs."""
    return create_effect_with_trace("gather.gather", programs, skip_frames=3)


def GatherDict(programs: Mapping[str, ProgramLike]) -> Effect:
    """Gather: Gather results from a dict of programs."""
    return create_effect_with_trace("gather.gather_dict", programs, skip_frames=3)


# No lowercase aliases to avoid confusion with the class name


__all__ = [
    "Gather",
    "GatherDict",
    "gather",
]
