from __future__ import annotations

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")
_HANDLER_HELP_URL = "https://docs.doeff.dev/handlers"


def _validate_do_handler_annotations(handlers) -> None:
    kleisli_mod = import_module("doeff.kleisli")
    validate_do_handler_effect_annotation = getattr(
        kleisli_mod, "validate_do_handler_effect_annotation"
    )
    for handler in handlers:
        if callable(handler):
            validate_do_handler_effect_annotation(handler)


def _format_handler_type_error(*, api_name: str, role: str, value: object) -> str:
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


def _coerce_handler(handler, *, api_name: str, role: str):
    if isinstance(handler, _ext.RustHandler):
        return handler
    if hasattr(_ext, "PyKleisli") and isinstance(handler, _ext.PyKleisli):
        return handler
    if isinstance(handler, _ext.DoeffGeneratorFn):
        return handler
    raise TypeError(_format_handler_type_error(api_name=api_name, role=role, value=handler))


def _coerce_handlers(handlers, *, api_name: str):
    return [_coerce_handler(handler, api_name=api_name, role="handler") for handler in handlers]


def _install_validated_runtime_api() -> None:
    if bool(getattr(_ext, "__doeff_handler_validation_patched__", False)):
        return

    raw_with_handler = _ext.WithHandler
    raw_with_intercept = _ext.WithIntercept
    raw_run = _ext.run
    raw_async_run = _ext.async_run
    raw_doexpr_to_generator = _ext.DoExpr.to_generator
    raw_nesting_to_generator = getattr(getattr(_ext, "_NestingStep", None), "to_generator", None)

    def validated_with_handler(handler, expr, return_clause=None):
        _validate_do_handler_annotations((handler,))
        coerced_handler = _coerce_handler(handler, api_name="WithHandler", role="handler")
        return raw_with_handler(coerced_handler, expr, return_clause)

    def validated_with_intercept(f, expr, types=None, mode="include", meta=None):
        coerced_interceptor = _coerce_handler(
            f,
            api_name="WithIntercept",
            role="interceptor",
        )
        return raw_with_intercept(
            coerced_interceptor,
            expr,
            types=types,
            mode=mode,
            meta=meta,
        )

    def validated_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        coerced_handlers = _coerce_handlers(handlers, api_name="run()")
        return raw_run(program, handlers=coerced_handlers, env=env, store=store, trace=trace)

    async def validated_async_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        coerced_handlers = _coerce_handlers(handlers, api_name="async_run()")
        return await raw_async_run(
            program,
            handlers=coerced_handlers,
            env=env,
            store=store,
            trace=trace,
        )

    def validated_doexpr_to_generator(self):
        from doeff.do import make_doeff_generator

        return make_doeff_generator(raw_doexpr_to_generator(self))

    def validated_nesting_to_generator(self):
        from doeff.do import make_doeff_generator

        assert raw_nesting_to_generator is not None
        return make_doeff_generator(raw_nesting_to_generator(self))

    setattr(_ext, "WithHandler", validated_with_handler)
    setattr(_ext, "WithIntercept", validated_with_intercept)
    setattr(_ext, "run", validated_run)
    setattr(_ext, "async_run", validated_async_run)
    setattr(_ext.DoExpr, "to_generator", validated_doexpr_to_generator)
    nesting_cls = getattr(_ext, "_NestingStep", None)
    if nesting_cls is not None and raw_nesting_to_generator is not None:
        setattr(nesting_cls, "to_generator", validated_nesting_to_generator)
    setattr(_ext, "__doeff_handler_validation_patched__", True)


_install_validated_runtime_api()


DoExpr = _ext.DoExpr
EffectBase = _ext.EffectBase
DoCtrlBase = _ext.DoCtrlBase
DoThunkBase = getattr(_ext, "DoThunkBase", None)
PyStdlib = _ext.PyStdlib
PySchedulerHandler = _ext.PySchedulerHandler
PyVM = _ext.PyVM
RunResult = _ext.RunResult
DoeffTracebackData = _ext.DoeffTracebackData
UnhandledEffectError = _ext.UnhandledEffectError
NoMatchingHandlerError = _ext.NoMatchingHandlerError
Ok = getattr(_ext, "Ok", None)
Err = getattr(_ext, "Err", None)
ResultOk = Ok
ResultErr = Err
K = _ext.K
DoeffGenerator = _ext.DoeffGenerator
DoeffGeneratorFn = _ext.DoeffGeneratorFn
PyKleisli = _ext.PyKleisli

try:
    from doeff.kleisli import KleisliProgram

    KleisliProgram.register(PyKleisli)
except Exception:
    pass


WithHandler = _ext.WithHandler
WithIntercept = _ext.WithIntercept


Pure = _ext.Pure
Apply = _ext.Apply
Expand = _ext.Expand
Map = _ext.Map
FlatMap = _ext.FlatMap
Eval = _ext.Eval
Perform = _ext.Perform
Finally = _ext.Finally
Resume = _ext.Resume
Delegate = _ext.Delegate
Pass = _ext.Pass
Transfer = _ext.Transfer
ResumeContinuation = _ext.ResumeContinuation
RustHandler = _ext.RustHandler


run = _ext.run


async_run = _ext.async_run


state = _ext.state
reader = _ext.reader
writer = _ext.writer
result_safe = _ext.result_safe
scheduler = _ext.scheduler
lazy_ask = _ext.lazy_ask
await_handler = _ext.await_handler
CreateContinuation = _ext.CreateContinuation
GetContinuation = _ext.GetContinuation
GetHandlers = _ext.GetHandlers
GetTraceback = _ext.GetTraceback
GetExecutionContext = _ext.GetExecutionContext
ExecutionContext = _ext.ExecutionContext
GetCallStack = _ext.GetCallStack
GetTrace = _ext.GetTrace
TraceFrame = _ext.TraceFrame
TraceHop = _ext.TraceHop
PythonAsyncSyntaxEscape = _ext.AsyncEscape
PyGet = _ext.PyGet
PyPut = _ext.PyPut
PyModify = _ext.PyModify
PyAsk = _ext.PyAsk
PyLocal = _ext.PyLocal
PyTell = _ext.PyTell
SpawnEffect = _ext.SpawnEffect
GatherEffect = _ext.GatherEffect
RaceEffect = _ext.RaceEffect
CreatePromiseEffect = _ext.CreatePromiseEffect
CompletePromiseEffect = _ext.CompletePromiseEffect
FailPromiseEffect = _ext.FailPromiseEffect
CreateExternalPromiseEffect = _ext.CreateExternalPromiseEffect
CreateSemaphoreEffect = _ext.CreateSemaphoreEffect
AcquireSemaphoreEffect = _ext.AcquireSemaphoreEffect
ReleaseSemaphoreEffect = _ext.ReleaseSemaphoreEffect
PythonAsyncioAwaitEffect = _ext.PythonAsyncioAwaitEffect
ResultSafeEffect = _ext.ResultSafeEffect
ProgramTraceEffect = _ext.ProgramTraceEffect
ProgramCallStackEffect = _ext.ProgramCallStackEffect
ProgramCallFrameEffect = _ext.ProgramCallFrameEffect
PyCancelEffect = _ext.PyCancelEffect
_SchedulerTaskCompleted = _ext._SchedulerTaskCompleted
_notify_semaphore_handle_dropped = _ext._notify_semaphore_handle_dropped
_debug_scheduler_semaphore_count = _ext._debug_scheduler_semaphore_count

# R13-I: DoExprTag constants
TAG_PURE = _ext.TAG_PURE
TAG_MAP = _ext.TAG_MAP
TAG_FLAT_MAP = _ext.TAG_FLAT_MAP
TAG_WITH_HANDLER = _ext.TAG_WITH_HANDLER
TAG_PERFORM = _ext.TAG_PERFORM
TAG_RESUME = _ext.TAG_RESUME
TAG_TRANSFER = _ext.TAG_TRANSFER
TAG_DELEGATE = _ext.TAG_DELEGATE
TAG_PASS = _ext.TAG_PASS
TAG_GET_CONTINUATION = _ext.TAG_GET_CONTINUATION
TAG_GET_HANDLERS = _ext.TAG_GET_HANDLERS
TAG_GET_TRACEBACK = _ext.TAG_GET_TRACEBACK
TAG_WITH_INTERCEPT = _ext.TAG_WITH_INTERCEPT
TAG_FINALLY = _ext.TAG_FINALLY
TAG_GET_CALL_STACK = _ext.TAG_GET_CALL_STACK
TAG_GET_TRACE = _ext.TAG_GET_TRACE
TAG_EVAL = _ext.TAG_EVAL
TAG_APPLY = _ext.TAG_APPLY
TAG_EXPAND = _ext.TAG_EXPAND
TAG_CREATE_CONTINUATION = _ext.TAG_CREATE_CONTINUATION
TAG_RESUME_CONTINUATION = _ext.TAG_RESUME_CONTINUATION
TAG_ASYNC_ESCAPE = _ext.TAG_ASYNC_ESCAPE
TAG_EFFECT = _ext.TAG_EFFECT
TAG_UNKNOWN = _ext.TAG_UNKNOWN

# SPEC-008 names
PySpawn = SpawnEffect
PyGather = GatherEffect
PyRace = RaceEffect
PyCreatePromise = CreatePromiseEffect
PyCompletePromise = CompletePromiseEffect
PyFailPromise = FailPromiseEffect
PyCreateExternalPromise = CreateExternalPromiseEffect
TaskCancelEffect = PyCancelEffect
PyTaskCompleted = _SchedulerTaskCompleted

__all__ = [
    "K",
    "Delegate",
    "Pass",
    "Apply",
    "Expand",
    "Eval",
    "Perform",
    "Finally",
    "Map",
    "FlatMap",
    "DoCtrlBase",
    "DoExpr",
    "DoeffGenerator",
    "DoeffGeneratorFn",
    "PyKleisli",
    "DoThunkBase",
    "EffectBase",
    "PyAsk",
    "PyLocal",
    "PyGet",
    "PySpawn",
    "PyGather",
    "PyRace",
    "PyCreatePromise",
    "PyCompletePromise",
    "PyFailPromise",
    "PyCreateExternalPromise",
    "PyCancelEffect",
    "PyTaskCompleted",
    "SpawnEffect",
    "GatherEffect",
    "RaceEffect",
    "CreatePromiseEffect",
    "CompletePromiseEffect",
    "FailPromiseEffect",
    "CreateExternalPromiseEffect",
    "CreateSemaphoreEffect",
    "AcquireSemaphoreEffect",
    "ReleaseSemaphoreEffect",
    "PythonAsyncioAwaitEffect",
    "ResultSafeEffect",
    "ProgramTraceEffect",
    "ProgramCallStackEffect",
    "ProgramCallFrameEffect",
    "TaskCancelEffect",
    "_SchedulerTaskCompleted",
    "PyModify",
    "PyPut",
    "PySchedulerHandler",
    "PyVM",
    "PyStdlib",
    "PyTell",
    "Pure",
    "Resume",
    "ResumeContinuation",
    "RunResult",
    "DoeffTracebackData",
    "UnhandledEffectError",
    "NoMatchingHandlerError",
    "RustHandler",
    "Transfer",
    "WithHandler",
    "WithIntercept",
    "PythonAsyncSyntaxEscape",
    "CreateContinuation",
    "GetCallStack",
    "GetTrace",
    "GetTraceback",
    "GetExecutionContext",
    "ExecutionContext",
    "GetContinuation",
    "GetHandlers",
    "TraceFrame",
    "TraceHop",
    "async_run",
    "reader",
    "run",
    "scheduler",
    "lazy_ask",
    "state",
    "result_safe",
    "await_handler",
    "writer",
    "TAG_PURE",
    "TAG_MAP",
    "TAG_FLAT_MAP",
    "TAG_WITH_HANDLER",
    "TAG_PERFORM",
    "TAG_RESUME",
    "TAG_TRANSFER",
    "TAG_DELEGATE",
    "TAG_PASS",
    "TAG_GET_CONTINUATION",
    "TAG_GET_HANDLERS",
    "TAG_GET_TRACEBACK",
    "TAG_WITH_INTERCEPT",
    "TAG_FINALLY",
    "TAG_GET_CALL_STACK",
    "TAG_GET_TRACE",
    "TAG_EVAL",
    "TAG_APPLY",
    "TAG_EXPAND",
    "TAG_CREATE_CONTINUATION",
    "TAG_RESUME_CONTINUATION",
    "TAG_ASYNC_ESCAPE",
    "TAG_EFFECT",
    "TAG_UNKNOWN",
]

if ResultOk is not None:
    __all__.append("Ok")
    __all__.append("ResultOk")
if ResultErr is not None:
    __all__.append("Err")
    __all__.append("ResultErr")
