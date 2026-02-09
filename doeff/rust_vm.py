from __future__ import annotations

from collections.abc import Sequence
import importlib
import inspect
from typing import Any


def _vm() -> Any:
    return importlib.import_module("doeff_vm")


def _coerce_program(program: Any) -> Any:
    vm = _vm()

    if isinstance(program, vm.EffectBase):
        return vm.Perform(program)

    if isinstance(program, vm.DoExpr):
        return program

    if inspect.isgeneratorfunction(program):
        raise TypeError("program must be DoExpr; got function. Did you mean to call it?")
    if inspect.isgenerator(program):
        raise TypeError("program must be DoExpr; got raw generator. Did you mean to wrap with @do?")
    if callable(program):
        raise TypeError("program must be DoExpr; got callable. Did you mean to call @do function?")
    raise TypeError(f"run() requires DoExpr[T] or EffectValue[T], got {type(program).__name__}")


def _raise_unhandled_effect_if_present(run_result: Any) -> Any:
    is_err = getattr(run_result, "is_err", None)
    if callable(is_err) and is_err():
        error = getattr(run_result, "error", None)
        if isinstance(error, TypeError):
            text = str(error).lower()
            if "unhandledeffect" in text or "unhandled effect" in text:
                raise error
    return run_result


def default_handlers() -> list[Any]:
    vm = _vm()
    required = ("state", "reader", "writer", "kpc")
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
    program = _coerce_program(program)
    result = run_fn(program, handlers=list(handlers), env=env, store=store)
    return _raise_unhandled_effect_if_present(result)


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
    program = _coerce_program(program)
    result = await run_fn(program, handlers=list(handlers), env=env, store=store)
    return _raise_unhandled_effect_if_present(result)


def __getattr__(name: str) -> Any:
    if name in {
        "RunResult",
        "WithHandler",
        "Pure",
        "Call",
        "Eval",
        "Perform",
        "Resume",
        "Delegate",
        "Transfer",
        "ResumeContinuation",
        "PythonAsyncSyntaxEscape",
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
    "Pure",
    "Call",
    "Eval",
    "Perform",
    "Resume",
    "Delegate",
    "Transfer",
    "ResumeContinuation",
    "PythonAsyncSyntaxEscape",
    "K",
    "state",
    "reader",
    "writer",
]
