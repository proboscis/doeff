"""doeff-run の検の実行環境(収集と解釈器の口だけ — 中身は interpreters.hy)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 解釈器は ``fake``(fake の handler + 仮想の時計)。docker・process・k3s の handler を足す card は ``interpreters.hy`` に名を足す。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff import Program


@pytest.fixture
def doeff_interpreter_name() -> str:
    return "plain"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str, tmp_path: Path) -> Callable[[Program], object]:
    from tests.interpreters import build_interpreter

    return build_interpreter(doeff_interpreter_name, tmp_path)
