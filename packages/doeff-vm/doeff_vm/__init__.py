# doeff_vm — Python bridge for the new VM architecture.

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")


def _name_types_in_messages_by_their_bare_name() -> None:
    """Python's messages name a type by its C-level name, which pyo3 builds from the pyclass
    module: "unsupported operand type(s) for +: 'doeff_vm.doeff_vm.Call' and 'int'". Setting
    __name__ makes that name the bare one, as for a class written in Python ('Call').
    __module__ and __qualname__, which pickle and the stored type names (cache keys, memo)
    read, stay."""
    for value in vars(_ext).values():
        if isinstance(value, type):
            value.__name__ = value.__name__


_name_types_in_messages_by_their_bare_name()

PyVM = _ext.PyVM
K = _ext.K
Callable = _ext.Callable
HandlerSpec = _ext.HandlerSpec
EffectBase = _ext.EffectBase
IRStream = _ext.IRStream
UnhandledEffect = _ext.UnhandledEffect
Ok = _ext.Ok
Err = _ext.Err

# DoExpr pyclasses
Pure = _ext.Pure
Perform = _ext.Perform
Resume = _ext.Resume
Transfer = _ext.Transfer
Apply = _ext.Apply
Expand = _ext.Expand
DoFunction = _ext.DoFunction
Call = _ext.Call
BindOpener = _ext.BindOpener
Pass = _ext.Pass
WithHandler = _ext.WithHandler
ResumeThrow = _ext.ResumeThrow
TransferThrow = _ext.TransferThrow
WithObserve = _ext.WithObserve
WithBoundaries = _ext.WithBoundaries
GetTraceback = _ext.GetTraceback
GetExecutionContext = _ext.GetExecutionContext
GetHandlers = _ext.GetHandlers
GetBoundaries = _ext.GetBoundaries
GetOuterHandlers = _ext.GetOuterHandlers
TailEval = _ext.TailEval

# The VM's counts and switches (live objects, work and scan counts, the invariant checks) are not
# exported here: read and set them with doeff's effects (ReadVmLiveCounts, SetInvariantChecks, ...),
# answered by doeff_core_effects.handlers.vm_probe_handler (agora card ki-27e8ea225b62).
