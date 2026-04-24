from __future__ import annotations

from importlib import import_module, util

import doeff_vm

import doeff



def test_get_trace_doctrl_removed() -> None:
    assert not hasattr(doeff_vm, "ProgramTraceEffect")
    assert not hasattr(doeff_vm, "GetTrace")
