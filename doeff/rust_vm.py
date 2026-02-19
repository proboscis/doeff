from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def _vm() -> Any:
    return importlib.import_module("doeff_vm")


def _is_generator_like(value: Any) -> bool:
    return inspect.isgenerator(value) or (
        hasattr(value, "__next__") and hasattr(value, "send") and hasattr(value, "throw")
    )


def _to_doeff_generator(candidate: Any, *, context: str) -> Any:
    vm = _vm()
    doeff_generator_type = getattr(vm, "DoeffGenerator", None)
    if doeff_generator_type is not None and isinstance(candidate, doeff_generator_type):
        return candidate

    if isinstance(candidate, vm.DoExpr):
        to_generator = getattr(candidate, "to_generator", None)
        if not callable(to_generator):
            raise TypeError(f"{context}: program has no callable to_generator()")
        generated = to_generator()
        return _to_doeff_generator(generated, context=f"{context}.to_generator()")

    if _is_generator_like(candidate):
        if doeff_generator_type is None:
            return candidate
        from doeff.do import make_doeff_generator

        return make_doeff_generator(candidate)

    raise TypeError(f"{context}: expected DoeffGenerator, got {type(candidate).__name__}")


def _wrap_python_handler(handler: Any) -> Any:
    if not callable(handler):
        return handler
    if getattr(handler, "__doeff_vm_wrapped_handler__", False):
        return handler

    def _wrapped(effect, k, _handler=handler):
        result = _handler(effect, k)
        if _is_generator_like(result):
            handler_name = getattr(_handler, "__qualname__", getattr(_handler, "__name__", "handler"))
            return _to_doeff_generator(
                result, context=f"handler {handler_name} return value"
            )
        return result

    if hasattr(handler, "__name__"):
        _wrapped.__name__ = handler.__name__
    if hasattr(handler, "__qualname__"):
        _wrapped.__qualname__ = handler.__qualname__
    if hasattr(handler, "__module__"):
        _wrapped.__module__ = handler.__module__
    if hasattr(handler, "__doc__"):
        _wrapped.__doc__ = handler.__doc__
    _wrapped.__wrapped__ = handler
    setattr(_wrapped, "__doeff_original_handler__", handler)

    _wrapped.__doeff_vm_wrapped_handler__ = True
    return _wrapped


def _coerce_program(program: Any) -> Any:
    vm = _vm()
    doeff_generator_type = getattr(vm, "DoeffGenerator", None)

    if doeff_generator_type is None:
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

    if isinstance(program, doeff_generator_type):
        return program

    if isinstance(program, vm.EffectBase):
        return _to_doeff_generator(vm.Perform(program), context="run(effect)")

    if isinstance(program, vm.DoExpr):
        return _to_doeff_generator(program, context="run(program)")

    if inspect.isgeneratorfunction(program):
        raise TypeError("program must be DoExpr; got function. Did you mean to call it?")
    if inspect.isgenerator(program):
        raise TypeError("program must be DoExpr; got raw generator. Did you mean to wrap with @do?")
    if callable(program):
        raise TypeError("program must be DoExpr; got callable. Did you mean to call @do function?")
    raise TypeError(f"run() requires DoExpr[T] or EffectValue[T], got {type(program).__name__}")


def _raise_unhandled_effect_if_present(run_result: Any, *, raise_unhandled: bool) -> Any:
    if not raise_unhandled:
        return run_result
    is_err = getattr(run_result, "is_err", None)
    if callable(is_err) and is_err():
        error = getattr(run_result, "error", None)
        if isinstance(error, TypeError):
            text = str(error).lower()
            if "unhandledeffect" in text or "unhandled effect" in text:
                raise error
    return run_result


def _build_doeff_traceback_if_present(run_result: Any) -> Any | None:
    is_err = getattr(run_result, "is_err", None)
    if not callable(is_err) or not is_err():
        return None
    error = getattr(run_result, "error", None)
    if not isinstance(error, BaseException):
        return None
    traceback_data = getattr(run_result, "traceback_data", None)
    if traceback_data is None:
        return None
    try:
        from doeff.traceback import attach_doeff_traceback

        doeff_tb = attach_doeff_traceback(error, traceback_data=traceback_data)
        if doeff_tb is not None:
            try:
                setattr(error, "doeff_traceback", doeff_tb)
            except Exception:
                pass
        return doeff_tb
    except Exception:
        # Best-effort: traceback projection should not block normal execution paths.
        return None


def _print_doeff_trace_if_present(run_result: Any) -> None:
    """Best-effort stderr printing for DoeffTraceback on error results."""
    doeff_tb = _build_doeff_traceback_if_present(run_result)
    if doeff_tb is None:
        return
    try:
        import sys

        print(doeff_tb.format_default(), file=sys.stderr)
    except Exception:
        return


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


def _normalize_env(env: dict[Any, Any] | None) -> dict[Any, Any] | None:
    if env is None:
        return None
    if not isinstance(env, dict):
        raise TypeError(f"env must be a dict, got {type(env).__name__}")
    return dict(env)


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


def _core_handler_sentinels(vm: Any) -> list[Any]:
    """Return the shared core handler sentinel stack from doeff_vm."""
    required = ("state", "reader", "writer", "result_safe", "scheduler", "lazy_ask")
    if all(hasattr(vm, name) for name in required):
        return [getattr(vm, name) for name in required]
    missing = [name for name in required if not hasattr(vm, name)]
    missing_txt = ", ".join(missing)
    raise RuntimeError(
        f"Installed doeff_vm module is missing required handler sentinels: {missing_txt}"
    )


def default_handlers() -> list[Any]:
    """Default sync preset.

    Handlers are user-space entities selected by the caller. run()/async_run()
    do not mutate this list.
    """
    vm = _vm()
    from doeff.effects.future import sync_await_handler

    return [*_core_handler_sentinels(vm), sync_await_handler]


def default_async_handlers() -> list[Any]:
    """Default async preset using event-loop aware Await handling."""
    vm = _vm()
    from doeff.effects.future import async_await_handler

    return [*_core_handler_sentinels(vm), async_await_handler]


def _wrap_with_handler_map(
    program: Any, handler_map: Mapping[type, Callable[[Any, Any], Any]]
) -> Any:
    """Wrap a program with typed WithHandler layers from an effect->handler mapping."""
    vm = _vm()
    with_handler = vm.WithHandler
    delegate = vm.Delegate

    wrapped = program
    if isinstance(wrapped, vm.EffectBase):
        wrapped = vm.Perform(wrapped)
    if not isinstance(wrapped, vm.DoExpr):
        raise TypeError(
            f"run_with_handler_map requires Program/DoExpr/Effect, got {type(wrapped).__name__}"
        )
    for effect_type, handler in reversed(list(handler_map.items())):

        def typed_handler(effect, k, _effect_type=effect_type, _handler=handler):
            if isinstance(effect, _effect_type):
                result = _handler(effect, k)
                if _is_generator_like(result):
                    return (yield from result)
                return result
            yield delegate()

        wrapped = with_handler(
            _wrap_python_handler(typed_handler),
            wrapped,
        )
    return wrapped


def run_with_handler_map(
    program: Any,
    handler_map: Mapping[type, Callable[[Any, Any], Any]],
    *,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
    print_doeff_trace: bool = True,
) -> Any:
    """Run with typed Python handlers plus the standard default handler sentinels."""
    wrapped = _wrap_with_handler_map(program, handler_map)
    return run(
        wrapped,
        handlers=default_handlers(),
        env=env,
        store=store,
        trace=trace,
        print_doeff_trace=print_doeff_trace,
    )


async def async_run_with_handler_map(
    program: Any,
    handler_map: Mapping[type, Callable[[Any, Any], Any]],
    *,
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
    print_doeff_trace: bool = True,
) -> Any:
    """Async counterpart to run_with_handler_map."""
    wrapped = _wrap_with_handler_map(program, handler_map)
    return await async_run(
        wrapped,
        handlers=default_async_handlers(),
        env=env,
        store=store,
        trace=trace,
        print_doeff_trace=print_doeff_trace,
    )


def run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
    print_doeff_trace: bool = True,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose run()")
    raise_unhandled = isinstance(program, vm.EffectBase)
    program = _coerce_program(program)
    kwargs = _run_call_kwargs(
        run_fn,
        handlers=handlers,
        env=_normalize_env(env),
        store=store,
        trace=trace,
    )
    result = _call_run_fn(run_fn, program, kwargs)
    if print_doeff_trace:
        _print_doeff_trace_if_present(result)
    return _raise_unhandled_effect_if_present(result, raise_unhandled=raise_unhandled)


async def async_run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
    print_doeff_trace: bool = True,
) -> Any:
    vm = _vm()
    run_fn = getattr(vm, "async_run", None)
    if run_fn is None:
        raise RuntimeError("Installed doeff_vm module does not expose async_run()")
    raise_unhandled = isinstance(program, vm.EffectBase)
    program = _coerce_program(program)
    kwargs = _run_call_kwargs(
        run_fn,
        handlers=handlers,
        env=_normalize_env(env),
        store=store,
        trace=trace,
    )
    result = await _call_async_run_fn(run_fn, program, kwargs)
    if print_doeff_trace:
        _print_doeff_trace_if_present(result)
    return _raise_unhandled_effect_if_present(result, raise_unhandled=raise_unhandled)


def __getattr__(name: str) -> Any:
    if name in {
        "RunResult",
        "DoeffTracebackData",
        "WithHandler",
        "Pure",
        "Call",
        "Eval",
        "Perform",
        "Resume",
        "Delegate",
        "Transfer",
        "ResumeContinuation",
        "GetTrace",
        "PythonAsyncSyntaxEscape",
        "K",
        "state",
        "reader",
        "writer",
        "result_safe",
        "lazy_ask",
        "await_handler",
    }:
        vm = _vm()
        if not hasattr(vm, name):
            raise AttributeError(f"doeff_vm has no attribute '{name}'")
        return getattr(vm, name)
    raise AttributeError(name)


__all__ = [
    "Call",
    "DoeffTracebackData",
    "Delegate",
    "Eval",
    "GetTrace",
    "K",
    "Perform",
    "Pure",
    "PythonAsyncSyntaxEscape",
    "Resume",
    "ResumeContinuation",
    "RunResult",
    "Transfer",
    "WithHandler",
    "async_run",
    "await_handler",
    "default_async_handlers",
    "default_handlers",
    "lazy_ask",
    "reader",
    "result_safe",
    "run",
    "state",
    "writer",
]
