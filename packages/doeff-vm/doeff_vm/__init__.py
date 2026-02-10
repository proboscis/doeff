from __future__ import annotations

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")

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
scheduler = _ext.scheduler
kpc = _ext.kpc
concurrent_kpc = _ext.concurrent_kpc
KleisliProgramCall = _ext.KleisliProgramCall
CreateContinuation = _ext.CreateContinuation
GetContinuation = _ext.GetContinuation
GetHandlers = _ext.GetHandlers
GetCallStack = _ext.GetCallStack
PythonAsyncSyntaxEscape = _ext.AsyncEscape
PyGet = _ext.PyGet
PyPut = _ext.PyPut
PyModify = _ext.PyModify
PyAsk = _ext.PyAsk
PyTell = _ext.PyTell
PyKPC = _ext.PyKPC
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
    "KleisliProgramCall",
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
    "PyKPC",
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
    "GetContinuation",
    "GetHandlers",
    "async_run",
    "concurrent_kpc",
    "kpc",
    "reader",
    "run",
    "scheduler",
    "state",
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
