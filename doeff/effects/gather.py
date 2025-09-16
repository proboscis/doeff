"""
Gather effects for parallel programs.

This module provides Gather effects for running multiple programs in parallel.
"""

from typing import Any, Dict

from .base import Effect, create_effect_with_trace


class gather:
    """Gather effects for parallel programs."""

    @staticmethod
    def gather(*programs: Any) -> Effect:
        """Gather results from multiple programs."""
        return create_effect_with_trace("gather.gather", programs)

    @staticmethod
    def gather_dict(programs: Dict[str, Any]) -> Effect:
        """Gather results from a dict of programs."""
        return create_effect_with_trace("gather.gather_dict", programs)


# Uppercase aliases
def Gather(*programs: Any) -> Effect:
    """Gather: Gather results from multiple programs."""
    return create_effect_with_trace("gather.gather", programs, skip_frames=3)


def GatherDict(programs: Dict[str, Any]) -> Effect:
    """Gather: Gather results from a dict of programs."""
    return create_effect_with_trace("gather.gather_dict", programs, skip_frames=3)


# No lowercase aliases to avoid confusion with the class name


__all__ = [
    "gather",
    "Gather",
    "GatherDict",
]