"""ADR-DOE-ENFORCE-001 R4: doeff の pytest は VM conformance oracle を常時有効にして走る。

B3 裁定(2026-07-14): oracle は per-step の実行時検査で、有効なら pytest スイート全体の VM 実行がそのまま
oracle の演習になる。検査はどの build にも入り、実行時に有効にする — root の
conftest.py が ini の値 vm_invariant_checks(既定 true)で有効にする(以前の cargo feature の build 分けは、同じ venv を
最後に組んだ経路で 15 倍速くも遅くもした)。

このテストは skip しない — oracle が無効な走行では hard fail する(偽緑の禁止、
ADR-DOE-ENFORCE-001 law `default-pytest-sees-all-enforcement`)。
"""

from doeff_core_effects import vm_probe_handler

from doeff import ReadInvariantChecks, run


def test_vm_built_with_invariant_checks():
    # VM バイナリが古ければ、vm_probe_handler が「`make sync` で再ビルドする」の ImportError で落ちる(stale Rust VM build)。
    assert run(vm_probe_handler(ReadInvariantChecks())), (
        "VM の oracle が無効 — ADR-DOE-ENFORCE-001 R4。root の conftest.py が"
        "ini の値 vm_invariant_checks で有効にするはず — -o vm_invariant_checks=false を渡していないか、conftest を通らずに走らせていないかを見ること"
    )


def test_a_child_cli_is_handed_the_session_oracle_explicitly():
    """失敗ケース: 親は環境変数に旗を書かないので、子の CLI を起こす部品が渡さなければ子は黙って検査なしで走る。

    tests/cli の 9 file が子を起こす 1 か所(cli_child.with_settings)が、この session の VM の状態を
    DOEFF_VM_INVARIANT_CHECKS として命令行に明示して置くこと。検が自分で渡した値はそちらが勝つ。
    """
    from tests.cli.cli_child import with_settings

    assert "DOEFF_VM_INVARIANT_CHECKS=1" in with_settings(["true"], {}), with_settings(["true"], {})
    assert "DOEFF_VM_INVARIANT_CHECKS=0" in with_settings(["true"], {"DOEFF_VM_INVARIANT_CHECKS": "0"})
