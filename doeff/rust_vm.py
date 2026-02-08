from __future__ import annotations

from collections.abc import Sequence
import importlib
import inspect
from typing import Any


def _vm() -> Any:
    return importlib.import_module("doeff_vm")


class _TopLevelDoExpr:
    def __init__(self, expr: Any):
        self._expr = expr

    def to_generator(self):
        value = yield self._expr
        return value


def _normalize_program(program: Any) -> Any:
    to_gen = inspect.getattr_static(program, "to_generator", None)
    if callable(to_gen):
        return program
    from doeff.types import EffectBase

    if isinstance(program, EffectBase):
        return _TopLevelDoExpr(program)
    raise TypeError("program must expose to_generator")


def default_handlers() -> list[Any]:
    vm = _vm()
    required = ("state", "reader", "writer")
    if all(hasattr(vm, name) for name in required):
        return [getattr(vm, name) for name in required]
    missing = [name for name in required if not hasattr(vm, name)]
    missing_txt = ", ".join(missing)
    raise RuntimeError(
        f"Installed doeff_vm module is missing required handler sentinels: {missing_txt}"
    )


def run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose run()")
    program = _normalize_program(program)
    return run_fn(program, handlers=list(handlers), env=env, store=store)


async def async_run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "async_run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose async_run()")
    program = _normalize_program(program)
    return await run_fn(program, handlers=list(handlers), env=env, store=store)


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
