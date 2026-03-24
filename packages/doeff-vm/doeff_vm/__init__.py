# doeff_vm — Python bridge for the new VM architecture.
# Old API is removed. Only PyVM and PyK are available.

from importlib import import_module

_ext = import_module("doeff_vm.doeff_vm")

PyVM = _ext.PyVM
K = _ext.K
