from __future__ import annotations

import asyncio
from collections.abc import Sequence
import importlib
import inspect
from typing import Any


def _vm() -> Any:
    pkg = importlib.import_module("doeff_vm")
    if hasattr(pkg, "run") and hasattr(pkg, "async_run"):
        return pkg
    try:
        ext = importlib.import_module("doeff_vm.doeff_vm")
    except ModuleNotFoundError:
        return pkg
    return ext


class _LegacyRunResult:
    def __init__(self, value: Any):
        self._value = value

    @property
    def value(self) -> Any:
        return self._value

    @property
    def error(self) -> None:
        return None

    @property
    def raw_store(self) -> dict[str, Any]:
        return {}

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False


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
    return _TopLevelDoExpr(program)


def default_handlers() -> list[Any]:
    vm = _vm()
    required = ("state", "reader", "writer")
    if all(hasattr(vm, name) for name in required):
        return [getattr(vm, name) for name in required]
    if hasattr(vm, "PyVM"):
        return ["state", "reader", "writer"]
    missing = [name for name in required if not hasattr(vm, name)]
    missing_txt = ", ".join(missing)
    raise RuntimeError(
        f"Installed doeff_vm module is missing required handler sentinels: {missing_txt}"
    )


def run(
    program: Any,
    handlers: Sequence[Any] | None = None,
    env: dict[str, Any] | None = None,
    store: dict[str, Any] | None = None,
) -> Any:
    program = _normalize_program(program)
    vm = _vm()
    run_fn = getattr(vm, "run", None)
    if run_fn is None and hasattr(vm, "PyVM"):
        selected = list(handlers) if handlers is not None else default_handlers()
        return _run_legacy_pyvm(program, vm=vm, handlers=selected, env=env, store=store)
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
    program = _normalize_program(program)
    vm = _vm()
    run_fn = getattr(vm, "async_run", None)
    if run_fn is None and hasattr(vm, "PyVM"):
        selected_handlers = list(handlers) if handlers is not None else default_handlers()
        return await asyncio.to_thread(
            _run_legacy_pyvm,
            program,
            vm=vm,
            handlers=selected_handlers,
            env=env,
            store=store,
        )
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose async_run()")
    selected_handlers = list(handlers) if handlers is not None else default_handlers()
    return await run_fn(program, handlers=selected_handlers, env=env, store=store)


def _run_legacy_pyvm(
    program: Any,
    *,
    vm: Any,
    handlers: Sequence[Any],
    env: dict[str, Any] | None,
    store: dict[str, Any] | None,
) -> _LegacyRunResult:
    if env is not None or store is not None:
        raise RuntimeError("Installed doeff_vm legacy API does not support env/store seeding")

    pyvm = vm.PyVM()
    stdlib = pyvm.stdlib()
    for h in handlers:
        if h == "state":
            _ = stdlib.state
            stdlib.install_state(pyvm)
        elif h == "reader":
            _ = stdlib.reader
            stdlib.install_reader(pyvm)
        elif h == "writer":
            _ = stdlib.writer
            stdlib.install_writer(pyvm)
        else:
            raise RuntimeError(
                "Installed doeff_vm legacy API supports only default state/reader/writer handlers"
            )
    return _LegacyRunResult(pyvm.run(program))


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
