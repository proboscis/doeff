"""tests/cli の検が子の CLI を起こす命令を組み立てる所(#2896)。

子の process は親の環境を継ぎ(写さない — 親の環境を読まない)、検が要る設定だけを `env NAME=VALUE …` で命令行に足す。
値が None の名は `env -u NAME` で子の環境から外す。PATH・HOME・uv の変数は継ぐことで子へ届く。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def with_settings(command: list[str], settings: Mapping[str, str | None]) -> list[str]:
    """command の前に `env …` を置き、子が継ぐ環境に settings だけを足す(None の名は外す)ため。"""
    removed = [part for name, value in settings.items() if value is None for part in ("-u", name)]
    added = [f"{name}={value}" for name, value in settings.items() if value is not None]
    return ["env", *removed, *added, *command]
