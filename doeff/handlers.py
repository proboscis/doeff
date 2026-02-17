"""doeff.handlers - Handler sentinels re-exported from doeff_vm.

These are user-space handler sentinels. The VM only dispatches to handlers and
does not own handler semantics.

Provides: state, reader, writer, result_safe, scheduler, lazy_ask, await_handler.
Default Await behavior for run()/async_run() comes from Python handlers in
doeff.effects.future via default handler presets.
"""

from __future__ import annotations

_HANDLER_SENTINELS = {
    "state",
    "reader",
    "writer",
    "result_safe",
    "scheduler",
    "lazy_ask",
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


__all__ = ["state", "reader", "writer", "result_safe", "scheduler", "lazy_ask", "await_handler"]
