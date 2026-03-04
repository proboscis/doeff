from __future__ import annotations

import doeff
import doeff_vm
import pytest


def test_program_trace_effect_removed() -> None:
    import doeff.effects as effects

    assert not hasattr(doeff, "ProgramTrace")
    assert not hasattr(effects, "ProgramTrace")

    import doeff.effects.trace as trace_effects

    if hasattr(trace_effects, "ProgramTrace"):
        with pytest.raises(NotImplementedError):
            trace_effects.ProgramTrace()


def test_get_trace_doctrl_removed() -> None:
    assert not hasattr(doeff_vm, "ProgramTraceEffect")
    assert not hasattr(doeff_vm, "GetTrace")
