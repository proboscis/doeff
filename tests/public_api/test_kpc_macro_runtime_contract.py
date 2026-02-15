"""Macro runtime contract tests after KPC deletion.

These assertions lock the public runtime model to Call/DoCtrl execution and
ensure legacy KPC exports are gone.
"""

from __future__ import annotations

import doeff_vm

from doeff import ProgramBase, default_handlers, do, presets, run


class TestNoLegacyKpcExports:
    def test_doeff_vm_has_no_kpc_symbol(self) -> None:
        assert not hasattr(doeff_vm, "kpc")

    def test_doeff_vm_has_no_concurrent_kpc_symbol(self) -> None:
        assert not hasattr(doeff_vm, "concurrent_kpc")

    def test_doeff_vm_has_no_kleisli_program_call_symbol(self) -> None:
        assert not hasattr(doeff_vm, "Kleisli" + "ProgramCall")


class TestDefaultAndPresetsRemainUsable:
    def test_default_handlers_contains_core_runtime_handlers(self) -> None:
        handlers = default_handlers()
        assert len(handlers) == 6

    def test_sync_preset_uses_runtime_handler_sentinels(self) -> None:
        names = [str(getattr(h, "name", repr(h))).lower() for h in presets.sync_preset]
        assert any("state" in n for n in names)
        assert any("reader" in n for n in names)
        assert any("writer" in n for n in names)

    def test_async_preset_uses_runtime_handler_sentinels(self) -> None:
        names = [str(getattr(h, "name", repr(h))).lower() for h in presets.async_preset]
        assert any("state" in n for n in names)
        assert any("scheduler" in n for n in names)


class TestCallDoctrlRuntimeContract:
    def test_do_call_returns_doctrl_runtime_value(self) -> None:
        @do
        def simple_program():
            return 1

        assert isinstance(simple_program(), ProgramBase)

    def test_do_program_runs_without_legacy_kpc_handler(self) -> None:
        @do
        def simple_program():
            value = yield doeff_vm.PyAsk("key")
            return value

        result = run(simple_program(), handlers=default_handlers(), env={"key": "test_value"})
        assert result.value == "test_value"
