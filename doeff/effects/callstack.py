"""Effects for introspecting the program call stack."""


import warnings

import doeff_vm

from ._validators import ensure_non_negative_int

ProgramCallFrameEffect = doeff_vm.ProgramCallFrameEffect
ProgramCallStackEffect = doeff_vm.ProgramCallStackEffect


def ProgramCallFrame(depth: int = 0) -> ProgramCallFrameEffect:
    """Create an effect that yields the ``CallFrame`` at the requested depth.

    Args:
        depth: ``0`` (default) yields the innermost frame (current program call).
            ``1`` yields the parent frame, and so on. ``IndexError`` is raised if
            the depth exceeds the available call stack.
    """

    ensure_non_negative_int(depth, name="depth")
    return ProgramCallFrameEffect(depth=depth)


def ProgramCallStack() -> ProgramCallStackEffect:
    """Create an effect that yields the current call stack as a tuple.

    Deprecated:
        Use ``ProgramTrace()`` for the full unified trace.
    """

    warnings.warn(
        "ProgramCallStack is deprecated; use ProgramTrace()",
        DeprecationWarning,
        stacklevel=2,
    )

    return ProgramCallStackEffect()


__all__ = [
    "ProgramCallFrame",
    "ProgramCallFrameEffect",
    "ProgramCallStack",
    "ProgramCallStackEffect",
]
