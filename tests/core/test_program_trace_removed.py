from __future__ import annotations

from importlib import import_module, util

import doeff_vm

import doeff


def test_program_trace_effect_removed() -> None:
    from doeff import effects

    assert not hasattr(doeff, "ProgramTrace")
    assert not hasattr(effects, "ProgramTrace")
    trace_spec = util.find_spec("doeff.effects.trace")
    if trace_spec is not None:
        trace_effects = import_module("doeff.effects.trace")
        assert not hasattr(trace_effects, "ProgramTrace")


def test_get_trace_doctrl_removed() -> None:
    assert not hasattr(doeff_vm, "ProgramTraceEffect")
    assert not hasattr(doeff_vm, "GetTrace")
