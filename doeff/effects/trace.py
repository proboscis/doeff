"""Trace introspection effects."""

from __future__ import annotations

from dataclasses import dataclass

from .base import EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class ProgramTraceEffect(EffectBase):
    """Return a snapshot of the current unified execution trace."""


def ProgramTrace() -> ProgramTraceEffect:
    """Create an effect that yields VM trace entries."""

    return create_effect_with_trace(ProgramTraceEffect())


__all__ = ["ProgramTrace", "ProgramTraceEffect"]
