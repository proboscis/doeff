"""doeff-codex の検の実行環境(Hy の module を import できるようにし、解釈器の口を 1 つ置く)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 行の分類の検は、版を固定した本物の codex(0.162.1)が手元の偽の上流に答えた stdout の行(``tests/recorded/codex-0.162.1``)を読む。
  録り方は ``scripts/record_app_server_lines.py``。
- この package の検は効果を出さない純関数と、替え玉の CLI を相手にする器の検だけなので、``doeff_interpreter`` は handler を被せずに
  Program を 1 回回す。
"""

from __future__ import annotations

from collections.abc import Callable

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff import Program, run


@pytest.fixture
def doeff_interpreter() -> Callable[[Program], object]:
    """deftest の Program を handler を被せずに 1 回回すため(検は効果を出さない — 出したら答え手が無くて落ちる)。"""
    return lambda program: run(program)
