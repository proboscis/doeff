from __future__ import annotations

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")

PyVM = _ext.PyVM
EffectBase = _ext.EffectBase
DoCtrlBase = _ext.DoCtrlBase
DoThunkBase = _ext.DoThunkBase
PyStdlib = _ext.PyStdlib
PySchedulerHandler = _ext.PySchedulerHandler
RunResult = _ext.RunResult
ResultOk = getattr(_ext, "ResultOk", None)
ResultErr = getattr(_ext, "ResultErr", None)
K = _ext.K
WithHandler = _ext.WithHandler
Resume = _ext.Resume
Delegate = _ext.Delegate
Transfer = _ext.Transfer
RustHandler = _ext.RustHandler
run = _ext.run
async_run = _ext.async_run
state = _ext.state
reader = _ext.reader
writer = _ext.writer

__all__ = [
    "K",
    "Delegate",
    "DoCtrlBase",
    "DoThunkBase",
    "EffectBase",
    "PySchedulerHandler",
    "PyStdlib",
    "PyVM",
    "Resume",
    "RunResult",
    "RustHandler",
    "Transfer",
    "WithHandler",
    "async_run",
    "reader",
    "run",
    "state",
    "writer",
]

if ResultOk is not None:
    __all__.append("ResultOk")
if ResultErr is not None:
    __all__.append("ResultErr")
