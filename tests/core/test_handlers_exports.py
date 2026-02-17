from __future__ import annotations

import doeff_vm
import pytest

import doeff.handlers as handlers


def test_result_safe_is_part_of_handlers_module_contract() -> None:
    assert "result_safe" in handlers.__all__
    assert "result_safe" in handlers._HANDLER_SENTINELS
    assert "result_safe" in (handlers.__doc__ or "")


def test_result_safe_resolves_to_doeff_vm_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hasattr(doeff_vm, "result_safe")
    monkeypatch.delitem(handlers.__dict__, "result_safe", raising=False)
    assert handlers.result_safe is doeff_vm.result_safe


def test_lazy_ask_is_part_of_handlers_module_contract() -> None:
    assert "lazy_ask" in handlers.__all__
    assert "lazy_ask" in handlers._HANDLER_SENTINELS
    assert "lazy_ask" in (handlers.__doc__ or "")


def test_lazy_ask_resolves_to_doeff_vm_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hasattr(doeff_vm, "lazy_ask")
    monkeypatch.delitem(handlers.__dict__, "lazy_ask", raising=False)
    assert handlers.lazy_ask is doeff_vm.lazy_ask
