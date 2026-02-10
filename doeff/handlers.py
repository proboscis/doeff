"""doeff.handlers - Handler sentinels re-exported from doeff_vm.

Provides: state, reader, writer, result_safe, scheduler, kpc, await_handler.
"""

from __future__ import annotations

_HANDLER_SENTINELS = {
    "state",
    "reader",
    "writer",
    "result_safe",
    "scheduler",
    "kpc",
    "await_handler",
}


def __getattr__(name: str):
    if name in _HANDLER_SENTINELS:
        import doeff_vm

        obj = getattr(doeff_vm, name, None)
        if obj is None:
            raise AttributeError(f"module 'doeff_vm' has no attribute {name!r}")
        globals()[name] = obj
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = tuple(sorted(_HANDLER_SENTINELS))
