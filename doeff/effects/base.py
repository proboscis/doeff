"""
Base imports for effect modules.

This module contains the common imports used across all effect modules.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from doeff.types import Effect, EffectBase, _intercept_value

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from doeff.program import Program


def intercept_value(
    value: Any, transform: Callable[[Effect], Effect | Program]
) -> Any:
    """Apply ``transform`` to any nested programs within ``value``."""

    return _intercept_value(value, transform)


__all__ = ["Effect", "EffectBase", "intercept_value"]
