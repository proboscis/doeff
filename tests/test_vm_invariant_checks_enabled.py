"""ADR-DOE-ENFORCE-001 R4: dev ビルドは VM conformance oracle(invariant-checks)を常時有効にする。

B3 裁定(2026-07-14): oracle は cargo feature `invariant-checks` 配下の per-step 実行時検査であり、
このフラグが有効なら pytest スイート全体の VM 実行がそのまま oracle の演習になる。

このテストは skip しない — invariant-checks 無効ビルドに対しては hard fail する(偽緑の禁止、
ADR-DOE-ENFORCE-001 law `default-pytest-sees-all-enforcement`)。release wheel を相手に
スイートを走らせた場合に落ちるのは意図した挙動である。
"""

import doeff_vm


def test_vm_built_with_invariant_checks():
    assert hasattr(doeff_vm, "invariant_checks_enabled"), (
        "doeff_vm.invariant_checks_enabled が存在しない — VM バイナリが古い。"
        "`make sync` で再ビルドすること(stale Rust VM build; CLAUDE.md の警告参照)"
    )
    assert doeff_vm.invariant_checks_enabled(), (
        "VM が invariant-checks 無効でビルドされている — ADR-DOE-ENFORCE-001 R4(B3 裁定 2026-07-14)。"
        "`make sync`(= maturin develop --release --features invariant-checks)で再ビルドすること"
    )
