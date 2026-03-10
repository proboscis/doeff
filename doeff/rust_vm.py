import importlib
import inspect
import types as py_types
import warnings
from collections.abc import Sequence
from typing import (
    Annotated,
    Any,
    ForwardRef,
    Protocol,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    runtime_checkable,
)


def _vm() -> Any:
    return importlib.import_module("doeff_vm")


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


_HANDLER_HELP_URL = "https://docs.doeff.dev/handlers"


def _format_handler_type_error(
    *,
    api_name: str,
    role: str,
    value: Any,
) -> str:
    got_value = f"{value!r} (type: {type(value).__name__})"
    if role == "handler":
        fix_block = (
            "  To fix, decorate your handler with @do:\n\n"
            "    from doeff import do\n"
            "    from doeff.effects.base import Effect\n\n"
            "    @do\n"
            "    def my_handler(effect: Effect, k):\n"
            "        ...\n"
            "        yield Resume(k, value)\n"
        )
    else:
        fix_block = (
            "  To fix, decorate your interceptor with @do:\n\n"
            "    from doeff import do\n"
            "    from doeff.effects.base import Effect\n\n"
            "    @do\n"
            "    def my_interceptor(effect: Effect):\n"
            "        return effect\n"
        )
    return (
        f"{api_name} {role} must be a @do decorated function, PyKleisli, or RustHandler.\n\n"
        f"  Got: {got_value}\n\n"
        f"{fix_block}\n"
        f"  See: {_HANDLER_HELP_URL}"
    )


def _coerce_handler(
    handler: Any,
    *,
    api_name: str,
    role: str,
) -> Any:
    vm = _vm()
    try:
        if isinstance(handler, vm.RustHandler):
            return handler
    except AttributeError:
        pass

    try:
        if isinstance(handler, vm.PyKleisli):
            return handler
    except AttributeError:
        pass

    try:
        if isinstance(handler, vm.DoeffGeneratorFn):
            return handler
    except AttributeError:
        pass

    raise TypeError(_format_handler_type_error(api_name=api_name, role=role, value=handler))


def _coerce_program(program: Any) -> Any:
    vm = _vm()
    if isinstance(program, vm.EffectBase):
        return vm.Perform(program)

    if isinstance(program, vm.DoExpr):
        return program

    try:
        doeff_generator_type = vm.DoeffGenerator
    except AttributeError:
        doeff_generator_type = None

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


def _normalize_intercept_types(types: Any) -> tuple[type[Any], ...] | None:
    if types is None:
        return None
    try:
        normalized = tuple(types)
    except TypeError as exc:
        raise TypeError("WithIntercept.types must be an iterable of type objects") from exc
    for typ in normalized:
        if not isinstance(typ, type):
            raise TypeError("WithIntercept.types must contain only Python type objects")
    return normalized


def _safe_annotations(target: Any) -> dict[str, Any] | None:
    if target is None:
        return None
    try:
        annotations = target.__annotations__
    except AttributeError:
        return None
    if isinstance(annotations, dict):
        return annotations
    return None


def _annotation_namespaces(target: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    globalns: dict[str, Any] = {}
    localns: dict[str, Any] = {}
    if target is None:
        return globalns, None

    module = inspect.getmodule(target)
    if module is not None:
        globalns.update(vars(module))

    try:
        target_globals = target.__globals__
    except AttributeError:
        target_globals = None
    if isinstance(target_globals, dict):
        globalns.update(target_globals)

    if callable(target):
        try:
            closurevars = inspect.getclosurevars(target)
        except (TypeError, ValueError):
            closurevars = None
        if closurevars is not None:
            globalns.update(closurevars.globals)
            localns.update(closurevars.nonlocals)

    return globalns, localns or None


def _resolve_annotation_only(annotation: Any, *targets: Any) -> Any:
    if annotation is inspect._empty:
        return annotation

    raw_annotation: str | None = None
    if isinstance(annotation, ForwardRef):
        raw_annotation = annotation.__forward_arg__
    elif isinstance(annotation, str):
        raw_annotation = annotation

    if raw_annotation is None:
        return annotation

    last_exc: Exception | None = None
    for target in targets:
        globalns, localns = _annotation_namespaces(target)
        try:
            return eval(raw_annotation, globalns, localns)
        except Exception as exc:
            last_exc = exc
    raise TypeError(f"Unresolved handler effect annotation: {raw_annotation!r}") from last_exc


def _safe_signature(target: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(target)
    except (TypeError, ValueError, NameError):
        # NameError: unresolved forward references on some Python versions.
        return None


def _safe_issubclass(candidate: Any, base: type[Any]) -> bool:
    try:
        return isinstance(candidate, type) and issubclass(candidate, base)
    except TypeError:
        return False


def _resolve_handler_effect_types(annotation: Any) -> tuple[type[Any], ...] | None:
    from doeff.types import Effect, EffectBase

    if annotation is inspect._empty:
        return None
    if annotation in (Any, Effect, EffectBase):
        return None
    if isinstance(annotation, ForwardRef):
        raise TypeError(
            f"Unresolved handler effect annotation: {annotation.__forward_arg__!r}"
        )
    if isinstance(annotation, str):
        raise TypeError(f"Unresolved handler effect annotation: {annotation!r}")

    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if not args:
            return None
        return _resolve_handler_effect_types(args[0])

    union_type = getattr(py_types, "UnionType", None)
    if origin is Union or (union_type is not None and origin is union_type):
        union_types: list[type[Any]] = []
        for arg in get_args(annotation):
            resolved = _resolve_handler_effect_types(arg)
            if resolved is None:
                # Any unresolvable or catch-all branch means "handle all".
                return None
            union_types.extend(resolved)
        if not union_types:
            return None
        return tuple(dict.fromkeys(union_types))

    if _safe_issubclass(annotation, EffectBase):
        return (annotation,)
    if _safe_issubclass(origin, EffectBase):
        return (origin,)

    return None


@runtime_checkable
class _HandlerWithFunc(Protocol):
    func: Any


@runtime_checkable
class _HandlerWithSignature(Protocol):
    __signature__: inspect.Signature


@runtime_checkable
class _HandlerWithMetadataSource(Protocol):
    _metadata_source: Any


def _extract_handler_effect_types(handler: Any) -> tuple[type[Any], ...] | None:
    """Extract effect filter types from a handler's first parameter annotation.

    Returns None when the annotation implies "handle all effects" or cannot be
    resolved safely.
    """
    func = handler.func if isinstance(handler, _HandlerWithFunc) else handler

    signature = handler.__signature__ if isinstance(handler, _HandlerWithSignature) else None
    if signature is None:
        signature = _safe_signature(func) or _safe_signature(handler)
    if signature is None:
        return None

    params = list(signature.parameters.values())
    if not params:
        return None

    effect_param = params[0]
    metadata_source = (
        handler._metadata_source if isinstance(handler, _HandlerWithMetadataSource) else None
    )
    handler_name, _, _ = _handler_registration_metadata(handler)
    raw_annotation = effect_param.annotation
    for candidate in (metadata_source, func, handler):
        annotations = _safe_annotations(candidate)
        if annotations is not None and effect_param.name in annotations:
            raw_annotation = annotations[effect_param.name]
            break
    try:
        annotation = _resolve_annotation_only(raw_annotation, metadata_source, func, handler)
        return _resolve_handler_effect_types(annotation)
    except TypeError as exc:
        raise TypeError(
            f"Failed to resolve effect annotation for handler {handler_name}: {exc}"
        ) from exc


def _with_intercept_metadata(interceptor: Any) -> dict[str, Any]:
    function_name, source_file, source_line = _handler_registration_metadata(interceptor)
    return {
        "function_name": function_name,
        "source_file": source_file,
        "source_line": source_line,
    }


def _unhandled_effect_error_types(vm: Any) -> tuple[type[BaseException], ...]:
    error_types: list[type[BaseException]] = []
    try:
        error_type = vm.UnhandledEffectError
        if isinstance(error_type, type) and issubclass(error_type, BaseException):
            error_types.append(error_type)
    except AttributeError:
        pass

    try:
        error_type = vm.NoMatchingHandlerError
        if isinstance(error_type, type) and issubclass(error_type, BaseException):
            error_types.append(error_type)
    except AttributeError:
        pass

    return tuple(error_types)


def _raise_unhandled_effect_if_present(run_result: Any, *, raise_unhandled: bool) -> Any:
    if not raise_unhandled:
        return run_result
    is_err = run_result.is_err
    if callable(is_err) and is_err():
        error = run_result.error
        vm = _vm()
        if isinstance(error, _unhandled_effect_error_types(vm)):
            raise error
    return run_result


def _build_doeff_traceback_if_present(run_result: Any) -> Any | None:
    try:
        is_err = run_result.is_err
    except AttributeError:
        return None
    if not callable(is_err) or not is_err():
        return None
    try:
        error = run_result.error
    except AttributeError:
        return None
    if not isinstance(error, BaseException):
        return None
    try:
        traceback_data = run_result.traceback_data
    except AttributeError:
        return None
    if traceback_data is None:
        return None
    try:
        from doeff.traceback import attach_doeff_traceback, set_attached_doeff_traceback

        doeff_tb = attach_doeff_traceback(error, traceback_data=traceback_data)
        if doeff_tb is not None:
            try:
                set_attached_doeff_traceback(error, doeff_tb)
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


def _print_doeff_trace(doeff_tb: Any | None) -> None:
    """Best-effort stderr printing for a built DoeffTraceback."""
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
    env: dict[str, Any] | None,
    store: dict[str, Any] | None,
    trace: bool,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "env": env,
        "store": store,
    }
    if not trace:
        return kwargs

    try:
        parameters = inspect.signature(run_fn).parameters
    except (TypeError, ValueError):
        text_signature = getattr(run_fn, "__text_signature__", None)
        if isinstance(text_signature, str) and "trace" in text_signature:
            kwargs["trace"] = True
        return kwargs

    if "trace" in parameters:
        kwargs["trace"] = True
        return kwargs

    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        kwargs["trace"] = True

    return kwargs


def _normalize_env(env: dict[Any, Any] | None) -> dict[Any, Any] | None:
    if env is None:
        return None
    if not isinstance(env, dict):
        raise TypeError(f"env must be a dict, got {type(env).__name__}")
    return dict(env)


def _call_run_fn(run_fn: Any, program: Any, kwargs: dict[str, Any]) -> Any:
    return run_fn(program, **kwargs)


async def _call_async_run_fn(run_fn: Any, program: Any, kwargs: dict[str, Any]) -> Any:
    return await run_fn(program, **kwargs)


def _core_handler_sentinels(vm: Any) -> list[Any]:
    """Return the shared core handler sentinel stack from doeff_vm."""
    required = ("state", "reader", "writer", "result_safe", "scheduler", "lazy_ask")
    try:
        return [
            vm.state,
            vm.reader,
            vm.writer,
            vm.result_safe,
            vm.scheduler,
            vm.lazy_ask,
        ]
    except AttributeError as exc:
        missing: list[str] = []
        for name in required:
            try:
                getattr(vm, name)
            except AttributeError:
                missing.append(name)
        missing_txt = ", ".join(missing)
        raise RuntimeError(
            f"Installed doeff_vm module is missing required handler sentinels: {missing_txt}"
        ) from exc


def default_handlers() -> list[Any]:
    """Default sync preset.

    Handlers are user-space entities selected by the caller. run()/async_run()
    do not mutate this list.
    """
    vm = _vm()
    from doeff.handlers.await_handlers import sync_await_handler
    from doeff.handlers.spawn_handler import spawn_intercept_handler

    return [
        *_core_handler_sentinels(vm),
        spawn_intercept_handler,
        sync_await_handler,
    ]


def default_async_handlers() -> list[Any]:
    """Default async preset using event-loop aware Await handling."""
    vm = _vm()
    from doeff.handlers.await_handlers import async_await_handler
    from doeff.handlers.spawn_handler import spawn_intercept_handler

    return [
        *_core_handler_sentinels(vm),
        spawn_intercept_handler,
        async_await_handler,
    ]


def _wrap_handlers(program: Any, handlers: Sequence[Any], *, api_name: str) -> Any:
    # Rust VM run() sets types=None (catch-all); wrapping here ensures annotation-based filtering.
    vm = _vm()
    for handler in reversed(handlers):
        handler = _coerce_handler(handler, api_name=api_name, role="handler")
        types = _extract_handler_effect_types(handler)
        program = vm.WithHandler(handler, program, types=types)
    return program


def run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
    print_doeff_trace: bool = False,
) -> Any:
    vm = _vm()
    try:
        run_fn = vm.run
    except AttributeError as exc:
        raise RuntimeError("Installed doeff_vm module does not expose run()") from exc
    raise_unhandled = isinstance(program, vm.EffectBase)
    program = _coerce_program(program)
    program = _wrap_handlers(program, handlers, api_name="run()")
    kwargs = _run_call_kwargs(
        run_fn,
        env=_normalize_env(env),
        store=store,
        trace=trace,
    )
    result = _call_run_fn(run_fn, program, kwargs)
    doeff_tb = _build_doeff_traceback_if_present(result)
    if print_doeff_trace:
        _print_doeff_trace(doeff_tb)
    return _raise_unhandled_effect_if_present(result, raise_unhandled=raise_unhandled)


async def async_run(
    program: Any,
    handlers: Sequence[Any] = (),
    env: dict[Any, Any] | None = None,
    store: dict[str, Any] | None = None,
    trace: bool = False,
    print_doeff_trace: bool = False,
) -> Any:
    vm = _vm()
    try:
        run_fn = vm.async_run
    except AttributeError as exc:
        raise RuntimeError("Installed doeff_vm module does not expose async_run()") from exc
    raise_unhandled = isinstance(program, vm.EffectBase)
    program = _coerce_program(program)
    program = _wrap_handlers(program, handlers, api_name="async_run()")
    kwargs = _run_call_kwargs(
        run_fn,
        env=_normalize_env(env),
        store=store,
        trace=trace,
    )
    result = await _call_async_run_fn(run_fn, program, kwargs)
    doeff_tb = _build_doeff_traceback_if_present(result)
    if print_doeff_trace:
        _print_doeff_trace(doeff_tb)
    return _raise_unhandled_effect_if_present(result, raise_unhandled=raise_unhandled)


def WithHandler(
    handler: Any,
    expr: Any,
) -> Any:
    handler = _coerce_handler(handler, api_name="WithHandler", role="handler")
    types = _extract_handler_effect_types(handler)
    vm = _vm()
    return vm.WithHandler(handler, expr, types=types)


def WithIntercept(
    f: Any,
    expr: Any,
    types: Any = None,
    mode: str = "include",
) -> Any:
    f = _coerce_handler(f, api_name="WithIntercept", role="interceptor")
    if mode not in {"include", "exclude"}:
        raise TypeError(f"WithIntercept.mode must be 'include' or 'exclude', got {mode!r}")

    normalized_types = _normalize_intercept_types(types)
    metadata = _with_intercept_metadata(f)

    vm = _vm()
    return vm.WithIntercept(
        f,
        expr,
        types=normalized_types,
        mode=mode,
        meta=metadata,
    )


_VM_LAZY_EXPORT_NAMES = {
    "RunResult",
    "DoeffTracebackData",
    "Pure",
    "Apply",
    "Expand",
    "Eval",
    "EvalInScope",
    "Perform",
    "Discontinue",
    "Pass",
    "Resume",
    "Delegate",
    "Transfer",
    "ResumeContinuation",
    "GetTraceback",
    "GetExecutionContext",
    "ExecutionContext",
    "TraceFrame",
    "TraceHop",
    "PythonAsyncSyntaxEscape",
    "UnhandledEffectError",
    "NoMatchingHandlerError",
    "K",
    "state",
    "reader",
    "writer",
    "result_safe",
    "lazy_ask",
    "await_handler",
}


def __getattr__(name: str) -> Any:
    if name == "pass_":
        vm = _vm()
        try:
            return vm.Pass
        except AttributeError as exc:
            raise AttributeError("doeff_vm has no attribute 'Pass'") from exc
    if name in _VM_LAZY_EXPORT_NAMES:
        vm = _vm()
        try:
            return getattr(vm, name)
        except AttributeError as exc:
            raise AttributeError(f"doeff_vm has no attribute '{name}'") from exc
    raise AttributeError(name)


__all__ = [
    "Apply",
    "Delegate",
    "Discontinue",
    "DoeffTracebackData",
    "Eval",
    "EvalInScope",
    "ExecutionContext",
    "Expand",
    "GetExecutionContext",
    "GetTraceback",
    "K",
    "NoMatchingHandlerError",
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
    "UnhandledEffectError",
    "WithHandler",
    "WithIntercept",
    "async_run",
    "await_handler",
    "default_async_handlers",
    "default_handlers",
    "lazy_ask",
    "pass_",
    "reader",
    "result_safe",
    "run",
    "state",
    "writer",
]
