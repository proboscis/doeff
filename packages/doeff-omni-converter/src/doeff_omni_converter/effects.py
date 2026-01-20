"""
Effects for doeff-omni-converter.

This module defines the ConvertEffect which represents a conversion request
that will be handled by the convert_handler using a rulebook.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doeff.types import Effect, EffectBase

if TYPE_CHECKING:
    from doeff.program import Program


@dataclass(frozen=True)
class ConvertEffect(EffectBase):
    """
    Effect requesting conversion of data from one format to another.

    This effect encapsulates a conversion request that will be resolved
    by the convert_handler using rules from the Reader environment.

    The handler will:
    1. Look up the rulebook from the environment
    2. Use A* search to find the optimal conversion path
    3. Execute the Kleisli converters in sequence
    4. Return an AutoData with the converted value and target format

    Example:
        >>> @do
        >>> def pipeline():
        >>>     img = AutoData(path, F.path)
        >>>     tensor = yield img.to(F.torch())  # Returns ConvertEffect
        >>>     return tensor.value
    """

    data: Any  # AutoData - using Any to avoid circular import
    target_format: Any  # Format

    def intercept(self, transform: Callable[[Effect], Effect | Program]) -> ConvertEffect:
        """ConvertEffect has no nested programs to intercept."""
        return self


def convert(data: Any, target: Any) -> ConvertEffect:
    """
    Create a ConvertEffect requesting conversion.

    This is the low-level function for creating convert effects.
    Prefer using AutoData.to() for a more fluent API.

    Args:
        data: AutoData instance to convert
        target: Target format specification

    Returns:
        ConvertEffect that will be handled by convert_handler
    """
    from doeff.utils import create_effect_with_trace

    return create_effect_with_trace(ConvertEffect(data=data, target_format=target))


__all__ = [
    "ConvertEffect",
    "convert",
]
