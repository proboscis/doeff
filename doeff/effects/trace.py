"""Trace introspection effects."""

from __future__ import annotations

import doeff_vm

ProgramTraceEffect = doeff_vm.ProgramTraceEffect


def ProgramTrace() -> ProgramTraceEffect:
    """Create an effect that yields VM trace entries."""

    return ProgramTraceEffect()


__all__ = ["ProgramTrace", "ProgramTraceEffect"]
