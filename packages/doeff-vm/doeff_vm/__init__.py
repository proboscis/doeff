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


def _install_validated_runtime_api() -> None:
    if bool(getattr(_ext, "__doeff_handler_validation_patched__", False)):
        return

    raw_with_handler = _ext.WithHandler
    raw_run = _ext.run
    raw_async_run = _ext.async_run

    def validated_with_handler(handler, expr):
        _validate_do_handler_annotations((handler,))
        return raw_with_handler(handler, expr)

    def validated_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        return raw_run(program, handlers=handlers, env=env, store=store, trace=trace)

    async def validated_async_run(program, handlers=(), env=None, store=None, trace=False):
        _validate_do_handler_annotations(handlers)
        return await raw_async_run(
            program,
            handlers=handlers,
            env=env,
            store=store,
            trace=trace,
        )

    setattr(_ext, "WithHandler", validated_with_handler)
    setattr(_ext, "run", validated_run)
    setattr(_ext, "async_run", validated_async_run)
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
Ok = getattr(_ext, "Ok", None)
Err = getattr(_ext, "Err", None)
ResultOk = Ok
ResultErr = Err
K = _ext.K


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
PyTell = _ext.PyTell
SpawnEffect = _ext.SpawnEffect
GatherEffect = _ext.GatherEffect
RaceEffect = _ext.RaceEffect
CreatePromiseEffect = _ext.CreatePromiseEffect
CompletePromiseEffect = _ext.CompletePromiseEffect
FailPromiseEffect = _ext.FailPromiseEffect
CreateExternalPromiseEffect = _ext.CreateExternalPromiseEffect
_SchedulerTaskCompleted = _ext._SchedulerTaskCompleted

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
    "DoThunkBase",
    "EffectBase",
    "PyAsk",
    "PyGet",
    "PySpawn",
    "PyGather",
    "PyRace",
    "PyCreatePromise",
    "PyCompletePromise",
    "PyFailPromise",
    "PyCreateExternalPromise",
    "PyTaskCompleted",
    "SpawnEffect",
    "GatherEffect",
    "RaceEffect",
    "CreatePromiseEffect",
    "CompletePromiseEffect",
    "FailPromiseEffect",
    "CreateExternalPromiseEffect",
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
