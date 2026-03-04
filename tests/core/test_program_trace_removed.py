from __future__ import annotations

import doeff_vm

import doeff


def test_program_trace_effect_removed() -> None:
    from doeff import effects
    from doeff.effects import trace as trace_effects

    assert not hasattr(doeff, "ProgramTrace")
    assert not hasattr(effects, "ProgramTrace")
    assert not hasattr(trace_effects, "ProgramTrace")


def test_get_trace_doctrl_removed() -> None:
    assert not hasattr(doeff_vm, "ProgramTraceEffect")
    assert not hasattr(doeff_vm, "GetTrace")
