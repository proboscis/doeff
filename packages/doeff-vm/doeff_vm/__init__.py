from __future__ import annotations

import inspect
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


def _is_generator_like(value) -> bool:
    return inspect.isgenerator(value) or (
        hasattr(value, "__next__") and hasattr(value, "send") and hasattr(value, "throw")
    )


def _handler_registration_metadata(handler):
    handler_qualname = getattr(handler, "__qualname__", None) or getattr(handler, "__name__", None)
    if handler_qualname is None:
        handler_qualname = type(handler).__name__
    if handler_qualname is None:
        handler_qualname = "<python_handler>"

    code_obj = getattr(handler, "__code__", None)
    if code_obj is None:
        call_method = getattr(handler, "__call__", None)
        code_obj = getattr(call_method, "__code__", None)

    source_file = getattr(code_obj, "co_filename", None)
    source_line = getattr(code_obj, "co_firstlineno", None)
    if not isinstance(source_line, int):
        source_line = None
    generator_name = getattr(code_obj, "co_name", None) or getattr(handler, "__name__", None)
    if generator_name is None:
        generator_name = "<handler>"
    return handler_qualname, source_file, source_line, generator_name


def _wrap_python_handler(handler):
    if not callable(handler):
        return handler
    if bool(getattr(handler, "__doeff_vm_wrapped_handler__", False)):
        return handler

    handler_name, handler_file, handler_line, generator_name = _handler_registration_metadata(
        handler
    )

    def _wrapped(effect, k, _handler=handler):
        result = _handler(effect, k)
        if _is_generator_like(result):
            from doeff.do import make_doeff_generator

            return make_doeff_generator(
                result,
                function_name=generator_name,
                source_file=handler_file,
                source_line=handler_line,
            )
        raise TypeError(
            f"Handler {handler_name} must return a generator, got {type(result).__name__}. "
            "Did you forget 'yield'?"
        )

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

    for attr in (
        "__doeff_do_decorated__",
        "__signature__",
        "__annotations__",
        "_metadata_source",
        "func",
    ):
        if hasattr(handler, attr):
            setattr(_wrapped, attr, getattr(handler, attr))
    setattr(_wrapped, "__doeff_vm_wrapped_handler__", True)
    setattr(_wrapped, "__doeff_handler_name__", handler_name)
    setattr(_wrapped, "__doeff_handler_file__", handler_file)
    setattr(_wrapped, "__doeff_handler_line__", handler_line)
    return _wrapped


def _wrap_handlers(handlers):
    return [_wrap_python_handler(handler) for handler in handlers]


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
        wrapped_handler = _wrap_python_handler(handler)
        return raw_with_handler(
            wrapped_handler,
            expr,
            handler_name=getattr(wrapped_handler, "__doeff_handler_name__", None),
            handler_file=getattr(wrapped_handler, "__doeff_handler_file__", None),
            handler_line=getattr(wrapped_handler, "__doeff_handler_line__", None),
        )

    def validated_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        wrapped_handlers = _wrap_handlers(handlers)
        return raw_run(program, handlers=wrapped_handlers, env=env, store=store, trace=trace)

    async def validated_async_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        wrapped_handlers = _wrap_handlers(handlers)
        return await raw_async_run(
            program,
            handlers=wrapped_handlers,
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


WithHandler = _ext.WithHandler


Pure = _ext.Pure
Call = _ext.Call
Map = _ext.Map
FlatMap = _ext.FlatMap
Eval = _ext.Eval
Perform = _ext.Perform
Resume = _ext.Resume
Delegate = _ext.Delegate
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
GetCallStack = _ext.GetCallStack
GetTrace = _ext.GetTrace
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
PyCancelEffect = _ext.PyCancelEffect
_SchedulerTaskCompleted = _ext._SchedulerTaskCompleted
_notify_semaphore_handle_dropped = _ext._notify_semaphore_handle_dropped
_debug_scheduler_semaphore_count = _ext._debug_scheduler_semaphore_count

# R13-I: DoExprTag constants
TAG_PURE = _ext.TAG_PURE
TAG_CALL = _ext.TAG_CALL
TAG_MAP = _ext.TAG_MAP
TAG_FLAT_MAP = _ext.TAG_FLAT_MAP
TAG_WITH_HANDLER = _ext.TAG_WITH_HANDLER
TAG_PERFORM = _ext.TAG_PERFORM
TAG_RESUME = _ext.TAG_RESUME
TAG_TRANSFER = _ext.TAG_TRANSFER
TAG_DELEGATE = _ext.TAG_DELEGATE
TAG_GET_CONTINUATION = _ext.TAG_GET_CONTINUATION
TAG_GET_HANDLERS = _ext.TAG_GET_HANDLERS
TAG_GET_CALL_STACK = _ext.TAG_GET_CALL_STACK
TAG_GET_TRACE = _ext.TAG_GET_TRACE
TAG_EVAL = _ext.TAG_EVAL
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
    "Call",
    "Eval",
    "Perform",
    "Map",
    "FlatMap",
    "DoCtrlBase",
    "DoExpr",
    "DoeffGenerator",
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
    "PythonAsyncSyntaxEscape",
    "CreateContinuation",
    "GetCallStack",
    "GetTrace",
    "GetContinuation",
    "GetHandlers",
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
    "TAG_CALL",
    "TAG_MAP",
    "TAG_FLAT_MAP",
    "TAG_WITH_HANDLER",
    "TAG_PERFORM",
    "TAG_RESUME",
    "TAG_TRANSFER",
    "TAG_DELEGATE",
    "TAG_GET_CONTINUATION",
    "TAG_GET_HANDLERS",
    "TAG_GET_CALL_STACK",
    "TAG_GET_TRACE",
    "TAG_EVAL",
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
