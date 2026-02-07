from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _vm() -> Any:
    import doeff_vm

    return doeff_vm


def default_handlers() -> list[Any]:
    vm = _vm()
    required = ("state", "reader", "writer")
    missing = [name for name in required if not hasattr(vm, name)]
    if missing:
        missing_txt = ", ".join(missing)
        raise RuntimeError(
            f"Installed doeff_vm module is missing required handler sentinels: {missing_txt}"
        )
    return [getattr(vm, name) for name in required]


def run(
    program: Any,
    handlers: Sequence[Any] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose run()")
    selected_handlers = list(handlers) if handlers is not None else default_handlers()
    return run_fn(program, handlers=selected_handlers, env=env, store=store)


async def async_run(
    program: Any,
    handlers: Sequence[Any] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "async_run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose async_run()")
    selected_handlers = list(handlers) if handlers is not None else default_handlers()
    return await run_fn(program, handlers=selected_handlers, env=env, store=store)


def __getattr__(name: str) -> Any:
    if name in {
        "RunResult",
        "WithHandler",
        "Resume",
        "Delegate",
        "Transfer",
        "K",
        "state",
        "reader",
        "writer",
    }:
        vm = _vm()
        if not hasattr(vm, name):
            raise AttributeError(f"doeff_vm has no attribute '{name}'")
        return getattr(vm, name)
    raise AttributeError(name)


__all__ = [
    "run",
    "async_run",
    "default_handlers",
    "RunResult",
    "WithHandler",
    "Resume",
    "Delegate",
    "Transfer",
    "K",
    "state",
    "reader",
    "writer",
]
