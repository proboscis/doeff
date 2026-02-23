from __future__ import annotations

import importlib
import inspect
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def _vm() -> Any:
    return importlib.import_module("doeff_vm")


def _is_generator_like(value: Any) -> bool:
    return inspect.isgenerator(value) or (
        hasattr(value, "__next__") and hasattr(value, "send") and hasattr(value, "throw")
    )


def _handler_registration_metadata(
    handler: Any,
) -> tuple[str, str, int]:
    handler_name = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", None)
    if handler_name is None:
        handler_name = type(handler).__name__
    if handler_name is None:
        handler_name = "<python_handler>"

    code_obj = getattr(handler, "__code__", None)
    if code_obj is None:
        call_method = getattr(handler, "__call__", None)
        code_obj = getattr(call_method, "__code__", None)

    source_file = getattr(code_obj, "co_filename", None) or "<unknown>"
    source_line = getattr(code_obj, "co_firstlineno", None)
    if not isinstance(source_line, int):
        source_line = 0
    return handler_name, source_file, source_line


def _coerce_handler(handler: Any) -> Any:
    vm = _vm()
    rust_handler_type = getattr(vm, "RustHandler", None)
    if rust_handler_type is not None and isinstance(handler, rust_handler_type):
        return handler

    doeff_generator_fn_type = getattr(vm, "DoeffGeneratorFn", None)
    if doeff_generator_fn_type is not None and isinstance(handler, doeff_generator_fn_type):
        return handler
    if not callable(handler):
        return handler

    if doeff_generator_fn_type is None:
        return handler

    from doeff.do import _default_get_frame

    handler_name, handler_file, handler_line = _handler_registration_metadata(handler)
    return vm.DoeffGeneratorFn(
        callable=handler,
        function_name=handler_name,
        source_file=handler_file,
        source_line=handler_line,
        get_frame=_default_get_frame,
    )


def _coerce_program(program: Any) -> Any:
    vm = _vm()
    if isinstance(program, vm.EffectBase):
        return vm.Perform(program)

    if isinstance(program, vm.DoExpr):
        return program

    doeff_generator_type = getattr(vm, "DoeffGenerator", None)
    if doeff_generator_type is not None and isinstance(program, doeff_generator_type):
        raise TypeError(
            "program must be DoExpr; got DoeffGenerator. "
            "Pass the DoExpr program object (not .to_generator())."
        )

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
    is_err = run_result.is_err
    if callable(is_err) and is_err():
        error = run_result.error
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
            except Exception as exc:
                warnings.warn(
                    f"Failed to attach doeff traceback to {type(error).__name__}: {exc}",
                    stacklevel=2,
                )
        return doeff_tb
    except Exception as exc:
        warnings.warn(f"Failed to build doeff traceback: {exc}", stacklevel=2)
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
    except Exception as exc:
        warnings.warn(f"Failed to print doeff trace: {exc}", stacklevel=2)
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
    from doeff.effects.spawn import spawn_intercept_handler

    return [
        *_core_handler_sentinels(vm),
        _coerce_handler(spawn_intercept_handler),
        _coerce_handler(sync_await_handler),
    ]


def default_async_handlers() -> list[Any]:
    """Default async preset using event-loop aware Await handling."""
    vm = _vm()
    from doeff.effects.future import async_await_handler
    from doeff.effects.spawn import spawn_intercept_handler

    return [
        *_core_handler_sentinels(vm),
        _coerce_handler(spawn_intercept_handler),
        _coerce_handler(async_await_handler),
    ]


def _wrap_with_handler_map(
    program: Any, handler_map: Mapping[type, Callable[[Any, Any], Any]]
) -> Any:
    """Wrap a program with typed WithHandler layers from an effect->handler mapping."""
    vm = _vm()
    with_handler = vm.WithHandler
    pass_through = vm.Pass

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
            yield pass_through()

        wrapped = with_handler(
            _coerce_handler(typed_handler),
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
    if name == "pass_":
        vm = _vm()
        if not hasattr(vm, "Pass"):
            raise AttributeError("doeff_vm has no attribute 'Pass'")
        return vm.Pass
    if name in {
        "RunResult",
        "DoeffTracebackData",
        "WithHandler",
        "Pure",
        "Apply",
        "Expand",
        "Eval",
        "Perform",
        "Pass",
        "Resume",
        "Delegate",
        "Transfer",
        "ResumeContinuation",
        "GetTraceback",
        "GetExecutionContext",
        "ExecutionContext",
        "GetTrace",
        "TraceFrame",
        "TraceHop",
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
    "Apply",
    "Expand",
    "DoeffTracebackData",
    "Delegate",
    "Eval",
    "GetTrace",
    "GetTraceback",
    "GetExecutionContext",
    "ExecutionContext",
    "K",
    "Pass",
    "Perform",
    "Pure",
    "PythonAsyncSyntaxEscape",
    "Resume",
    "ResumeContinuation",
    "RunResult",
    "TraceFrame",
    "TraceHop",
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
    "pass_",
    "state",
    "writer",
]
