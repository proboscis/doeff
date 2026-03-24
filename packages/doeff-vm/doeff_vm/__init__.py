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
