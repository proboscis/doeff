"""doeff-claude-code の検の実行環境(収集と解釈器の口だけ — 中身は interpreters.hy)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 解釈器は 3 つ(``fake`` / ``stub`` / ``real``)。``real``(本物の claude)は印 ``e2e`` を付け、env
  ``DOEFF_CLAUDE_CODE_REAL_CONFIG_DIR`` が無ければ skip する。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff import Program


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    from tests.interpreters import real_marker_needed

    for item in items:
        callspec = getattr(item, "callspec", None)
        if callspec is not None and real_marker_needed(callspec.params.get("doeff_interpreter_name")):
            item.add_marker(pytest.mark.e2e)
            item.add_marker(pytest.mark.slow)


@pytest.fixture
def doeff_interpreter_name() -> str:
    return "plain"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str, tmp_path: Path) -> Callable[[Program], object]:
    from tests.interpreters import build_interpreter, skip_reason

    reason = skip_reason(doeff_interpreter_name)
    if reason:
        pytest.skip(reason)
    return build_interpreter(doeff_interpreter_name, tmp_path)
