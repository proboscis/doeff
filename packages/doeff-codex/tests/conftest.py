"""doeff-codex の検の実行環境(Hy の module を import できるようにし、解釈器の口を置く — 中身は interpreters.hy)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 行の分類の検は、版を固定した本物の codex(0.162.1)が手元の偽の上流に答えた stdout の行(``tests/recorded/codex-0.162.1``)を読む。
  録り方は ``scripts/record_app_server_lines.py``。
- 解釈器は 3 つ(``plain`` / ``fake`` / ``stub``)。筋書きの検は頭の ``{:interpreters ["fake" "stub"]}`` で両方に当たり、ほかの検は
  ``plain``(handler を被せない)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff import Program


@pytest.fixture
def doeff_interpreter_name() -> str:
    """筋書きの頭で名指さない検の解釈器の名(handler を被せない純関数の検)。"""
    return "plain"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str, tmp_path: Path) -> Callable[[Program], object]:
    """deftest の Program を、名指された解釈器(handler の組)で 1 回回すため。"""
    from tests.interpreters import build_interpreter

    return build_interpreter(doeff_interpreter_name, tmp_path)
