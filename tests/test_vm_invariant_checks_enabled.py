"""ADR-DOE-ENFORCE-001 R4: doeff の pytest は VM conformance oracle を常時有効にして走る。

B3 裁定(2026-07-14): oracle は per-step の実行時検査で、有効なら pytest スイート全体の VM 実行がそのまま
oracle の演習になる。agora-redesign #980 から検査はどの build にも入り、実行時に有効にする — root の
conftest.py が DOEFF_VM_INVARIANT_CHECKS=1 にする(以前の cargo feature の build 分けは、同じ venv を
最後に組んだ経路で 15 倍速くも遅くもした)。

このテストは skip しない — oracle が無効な走行では hard fail する(偽緑の禁止、
ADR-DOE-ENFORCE-001 law `default-pytest-sees-all-enforcement`)。
"""

import doeff_vm


def test_vm_built_with_invariant_checks():
    assert hasattr(doeff_vm, "invariant_checks_enabled"), (
        "doeff_vm.invariant_checks_enabled が存在しない — VM バイナリが古い。"
        "`make sync` で再ビルドすること(stale Rust VM build; CLAUDE.md の警告参照)"
    )
    assert doeff_vm.invariant_checks_enabled(), (
        "VM の oracle が無効 — ADR-DOE-ENFORCE-001 R4(B3 裁定 2026-07-14)。root の conftest.py が"
        "DOEFF_VM_INVARIANT_CHECKS=1 にするはず — 0 を渡していないか、conftest を通らずに走らせていないかを見ること"
    )
