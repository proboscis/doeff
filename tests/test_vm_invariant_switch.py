"""VM の不変条件の検査は、どの build でも実行時の切り替えで有効・無効が決まる(agora-redesign #980)。

以前は cargo feature で build を分け、同じ venv が最後に組んだ経路(make sync / uv sync)で 15 倍速さを変えた。
いまは検査がどの build にも入り、DOEFF_VM_INVARIANT_CHECKS(拡張が最初に 1 回読む)か
set_invariant_checks で決まる。この検は、同じ build が変数だけで両方の状態になることを子 process で見る。
"""

import subprocess
import sys

import pytest

PROBE = (
    "from doeff import do, run\n"
    "from doeff_vm.doeff_vm import invariant_checks_enabled\n"
    "@do\n"
    "def one():\n"
    "    return 1\n"
    "assert run(one()) == 1\n"
    "print(invariant_checks_enabled())\n"
)


def _probe(value: str | None) -> subprocess.CompletedProcess[str]:
    """変数を与えた(または外した)子 process で 1 つの program を走らせ、検査の状態を読むため。"""
    # 子はこの process の環境を継ぐ — 変数を外し(`env -u`)、value が在ればその値で置く。
    setting = [] if value is None else [f"DOEFF_VM_INVARIANT_CHECKS={value}"]
    return subprocess.run(
        ["env", "-u", "DOEFF_VM_INVARIANT_CHECKS", *setting, sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(("value", "expected"), [(None, "False"), ("0", "False"), ("1", "True")])
def test_env_decides_the_checks_on_the_same_build(value: str | None, expected: str) -> None:
    result = _probe(value)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == expected


def test_unknown_value_is_refused() -> None:
    result = _probe("yes")
    assert result.returncode != 0
    assert "DOEFF_VM_INVARIANT_CHECKS" in result.stderr


def test_setter_turns_the_checks_on_and_off() -> None:
    from doeff_vm.doeff_vm import invariant_checks_enabled, set_invariant_checks

    before = invariant_checks_enabled()
    try:
        set_invariant_checks(False)
        assert not invariant_checks_enabled()
        set_invariant_checks(True)
        assert invariant_checks_enabled()
    finally:
        set_invariant_checks(before)
