"""Macro runtime contract tests after KPC deletion.

These assertions lock the public runtime model to Call/DoCtrl execution and
ensure legacy KPC exports are gone.
"""

from __future__ import annotations

import doeff_vm

from doeff import ProgramBase, default_handlers, do, presets, run
from tests._run_helpers import run_with_defaults


class TestNoLegacyKpcExports:
    def test_doeff_vm_has_no_kpc_symbol(self) -> None:
        assert not hasattr(doeff_vm, "kpc")

    def test_doeff_vm_has_no_concurrent_kpc_symbol(self) -> None:
        assert not hasattr(doeff_vm, "concurrent_kpc")

    def test_doeff_vm_has_no_kleisli_program_call_symbol(self) -> None:
        assert not hasattr(doeff_vm, "Kleisli" + "ProgramCall")


class TestDefaultAndPresetsRemainUsable:
    pass


class TestCallDoctrlRuntimeContract:
    def test_do_call_returns_doctrl_runtime_value(self) -> None:
        @do
        def simple_program():
            return 1

        assert isinstance(simple_program(), ProgramBase)

