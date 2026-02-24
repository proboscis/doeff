from __future__ import annotations

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")


def _validate_do_handler_annotations(handlers) -> None:
    kleisli_mod = import_module("doeff.kleisli")
    validate_do_handler_effect_annotation = getattr(
        kleisli_mod, "validate_do_handler_effect_annotation"
    )
    for handler in handlers:
        if callable(handler):
            validate_do_handler_effect_annotation(handler)


def _handler_registration_metadata(handler):
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


def _coerce_handler(handler):
    if isinstance(handler, _ext.RustHandler):
        return handler
    if isinstance(handler, _ext.DoeffGeneratorFn):
        return handler
    if not callable(handler):
        return handler

    from doeff.do import _default_get_frame

    handler_name, source_file, source_line = _handler_registration_metadata(handler)
    return _ext.DoeffGeneratorFn(
        callable=handler,
        function_name=handler_name,
        source_file=source_file,
        source_line=source_line,
        get_frame=_default_get_frame,
    )


def _coerce_handlers(handlers):
    return [_coerce_handler(handler) for handler in handlers]


def _install_validated_runtime_api() -> None:
    if bool(getattr(_ext, "__doeff_handler_validation_patched__", False)):
        return

    raw_with_handler = _ext.WithHandler
    raw_run = _ext.run
    raw_async_run = _ext.async_run
    raw_doexpr_to_generator = _ext.DoExpr.to_generator
    raw_nesting_to_generator = getattr(getattr(_ext, "_NestingStep", None), "to_generator", None)

    def validated_with_handler(handler, expr):
        _validate_do_handler_annotations((handler,))
        coerced_handler = _coerce_handler(handler)
        return raw_with_handler(coerced_handler, expr)

    def validated_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        coerced_handlers = _coerce_handlers(handlers)
        return raw_run(program, handlers=coerced_handlers, env=env, store=store, trace=trace)

    async def validated_async_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        coerced_handlers = _coerce_handlers(handlers)
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
Ok = getattr(_ext, "Ok", None)
Err = getattr(_ext, "Err", None)
ResultOk = Ok
ResultErr = Err
K = _ext.K
DoeffGenerator = _ext.DoeffGenerator
DoeffGeneratorFn = _ext.DoeffGeneratorFn


WithHandler = _ext.WithHandler
WithIntercept = _ext.WithIntercept


Pure = _ext.Pure
Apply = _ext.Apply
Expand = _ext.Expand
Map = _ext.Map
FlatMap = _ext.FlatMap
Eval = _ext.Eval
Perform = _ext.Perform
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
    "Map",
    "FlatMap",
    "DoCtrlBase",
    "DoExpr",
    "DoeffGenerator",
    "DoeffGeneratorFn",
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
