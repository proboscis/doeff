from __future__ import annotations

import doeff_vm


def test_get_trace_doctrl_removed() -> None:
    assert not hasattr(doeff_vm, "ProgramTraceEffect")
    assert not hasattr(doeff_vm, "GetTrace")
