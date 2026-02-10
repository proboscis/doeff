from __future__ import annotations

from collections.abc import Sequence
import concurrent.futures
import importlib
import inspect
import asyncio
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


def _run_awaitable_blocking(awaitable: Any) -> Any:
    def _runner() -> Any:
        return asyncio.run(awaitable)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


def _run_call_kwargs(
    run_fn: Any,
    *,
    handlers: Sequence[Any],
    env: dict[str, Any] | None,
    store: dict[str, Any] | None,
    trace: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "handlers": list(handlers),
        "env": env,
        "store": store,
    }

    try:
        parameters = inspect.signature(run_fn).parameters
    except (TypeError, ValueError):
        kwargs["trace"] = trace
        return kwargs

    if "trace" in parameters:
        kwargs["trace"] = trace
        return kwargs

    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        kwargs["trace"] = trace

    return kwargs


def _normalize_env(env: dict[Any, Any] | None) -> dict[str, Any] | None:
    if env is None:
        return None
    normalized: dict[str, Any] = {}
    for key, value in env.items():
        if isinstance(key, str):
            normalized[key] = value
        else:
            normalized[str(key)] = value
    return normalized


def _is_unexpected_trace_keyword(exc: TypeError) -> bool:
    message = str(exc)
    return "trace" in message and "unexpected keyword" in message


def _call_run_fn(run_fn: Any, program: Any, kwargs: dict[str, Any]) -> Any:
    try:
        return run_fn(program, **kwargs)
    except TypeError as exc:
        if "trace" in kwargs and _is_unexpected_trace_keyword(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("trace", None)
            return run_fn(program, **retry_kwargs)
        raise


async def _call_async_run_fn(run_fn: Any, program: Any, kwargs: dict[str, Any]) -> Any:
    try:
        return await run_fn(program, **kwargs)
    except TypeError as exc:
        if "trace" in kwargs and _is_unexpected_trace_keyword(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("trace", None)
            return await run_fn(program, **retry_kwargs)
        raise


def _await_handler(effect: Any, k: Any):
    from doeff.effects.future import PythonAsyncioAwaitEffect

    if isinstance(effect, PythonAsyncioAwaitEffect):
        result = _run_awaitable_blocking(effect.awaitable)
        return (yield _vm().Resume(k, result))
    yield _vm().Delegate()


def default_handlers() -> list[Any]:
    vm = _vm()
    required = ("state", "reader", "writer", "scheduler", "kpc")
    if all(hasattr(vm, name) for name in required):
        return [getattr(vm, name) for name in required] + [_await_handler]
    missing = [name for name in required if not hasattr(vm, name)]
    missing_txt = ", ".join(missing)
    raise RuntimeError(
        f"Installed doeff_vm module is missing required handler sentinels: {missing_txt}"
    )


def run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose run()")
    program = _coerce_program(program)
    kwargs = _run_call_kwargs(
        run_fn,
        handlers=handlers,
        env=_normalize_env(env),
        store=store,
        trace=trace,
    )
    result = _call_run_fn(run_fn, program, kwargs)
    return _raise_unhandled_effect_if_present(result)


async def async_run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "async_run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose async_run()")
    program = _coerce_program(program)
    kwargs = _run_call_kwargs(
        run_fn,
        handlers=handlers,
        env=_normalize_env(env),
        store=store,
        trace=trace,
    )
    result = await _call_async_run_fn(run_fn, program, kwargs)
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
