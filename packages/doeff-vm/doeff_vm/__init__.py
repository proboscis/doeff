from __future__ import annotations

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")

EffectBase = _ext.EffectBase
DoCtrlBase = _ext.DoCtrlBase
DoThunkBase = getattr(_ext, "DoThunkBase", None)
PyStdlib = _ext.PyStdlib
PySchedulerHandler = _ext.PySchedulerHandler
RunResult = _ext.RunResult
ResultOk = getattr(_ext, "ResultOk", None)
ResultErr = getattr(_ext, "ResultErr", None)
K = _ext.K
WithHandler = _ext.WithHandler
Map = _ext.Map
FlatMap = _ext.FlatMap
Resume = _ext.Resume
Delegate = _ext.Delegate
Transfer = _ext.Transfer
RustHandler = _ext.RustHandler
run = _ext.run
async_run = _ext.async_run
state = _ext.state
reader = _ext.reader
writer = _ext.writer
scheduler = _ext.scheduler
kpc = _ext.kpc
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

__all__ = [
    "K",
    "KleisliProgramCall",
    "Delegate",
    "Map",
    "FlatMap",
    "DoCtrlBase",
    "DoThunkBase",
    "EffectBase",
    "PyAsk",
    "PyGet",
    "PyKPC",
    "PyModify",
    "PyPut",
    "PySchedulerHandler",
    "PyStdlib",
    "PyTell",
    "Resume",
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
    "kpc",
    "reader",
    "run",
    "scheduler",
    "state",
    "writer",
]

if ResultOk is not None:
    __all__.append("ResultOk")
if ResultErr is not None:
    __all__.append("ResultErr")
