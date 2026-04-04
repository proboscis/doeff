# doeff_vm — Python bridge for the new VM architecture.

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")

PyVM = _ext.PyVM
K = _ext.K
Callable = _ext.Callable
EffectBase = _ext.EffectBase
IRStream = _ext.IRStream
Ok = _ext.Ok
Err = _ext.Err

# DoExpr pyclasses
Pure = _ext.Pure
Perform = _ext.Perform
Resume = _ext.Resume
Transfer = _ext.Transfer
Apply = _ext.Apply
Expand = _ext.Expand
Pass = _ext.Pass
WithHandler = _ext.WithHandler
ResumeThrow = _ext.ResumeThrow
TransferThrow = _ext.TransferThrow
WithObserve = _ext.WithObserve
GetTraceback = _ext.GetTraceback
GetExecutionContext = _ext.GetExecutionContext
GetHandlers = _ext.GetHandlers
TailEval = _ext.TailEval

vm_live_counts = _ext.vm_live_counts
