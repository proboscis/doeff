"""tests/cli の検が子の CLI を起こす命令を組み立てる所(#2896)。

子の process は親の環境を継ぎ(写さない — 親の環境を読まない)、検が要る設定だけを `env NAME=VALUE …` で命令行に足す。
値が None の名は `env -u NAME` で子の環境から外す。PATH・HOME・uv の変数は継ぐことで子へ届く。
VM の検査の旗(DOEFF_VM_INVARIANT_CHECKS)は、この session の VM の状態(root の conftest が ini の値 vm_invariant_checks で入れる)を
毎回明示して渡す — 親は環境変数に書かないので、継ぐだけでは子が黙って検査なしで走る(ADR-DOE-ENFORCE-001 R4・agora-redesign #3012)。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from doeff_vm.doeff_vm import invariant_checks_enabled

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def with_settings(command: list[str], settings: Mapping[str, str | None]) -> list[str]:
    """command の前に `env …` を置き、子が継ぐ環境に settings だけを足す(None の名は外す)ため。"""
    removed = [part for name, value in settings.items() if value is None for part in ("-u", name)]
    oracle = {"DOEFF_VM_INVARIANT_CHECKS": "1" if invariant_checks_enabled() else "0"}
    added = [f"{name}={value}" for name, value in {**oracle, **settings}.items() if value is not None]
    return ["env", *removed, *added, *command]
