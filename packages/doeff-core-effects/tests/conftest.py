"""doeff-core-effects の検の実行環境(Hy の ``test_*.hy`` の収集と deftest の解釈器)。

- doeff-adr の Hy の file の収集(``DoeffAdrHyFile``)をこの dir の中だけで使う(doeff-cluster の tests/conftest.py と同じ形 — 根の ini の
  ``doeff_adr_hy_files`` には足さない。package の母集団は ``make test-packages`` が別に走らせる)。
- 契約テスト(agora-redesign #1159)は deftest の ``:interpreters`` で handler を差し替える。名 → 組み立ての表は
  stop_contract_handlers.hy と http_contract_handlers.hy が持ち、ここはその表を引いて scheduler つきで 1 回回すだけ。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - lets the Hy composition modules (*_contract_handlers.hy) be imported
import pytest
from doeff_adr.pytest_plugin import DoeffAdrHyFile
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run

TESTS_DIR = Path(__file__).resolve().parent

# TESTS_DIR makes the uniquely-named composition modules importable even when this suite is collected together
# with other testpaths (pytest only prepends a test file's own directory lazily).
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# 契約テストでない deftest の名: handler は各 deftest が本体の中で被せる(test_sql_effects.hy)。
PLAIN = "plain"


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture
def doeff_interpreter_name() -> str:
    return PLAIN


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    """deftest の Program を、:interpreters の名の handler の組の下で scheduler つきで 1 回回す。"""
    from http_contract_handlers import INTERPRETERS as HTTP_INTERPRETERS
    from stop_contract_handlers import INTERPRETERS as STOP_INTERPRETERS

    compositions: dict[str, Callable[[Program], Program]] = {
        PLAIN: lambda program: program,
        **STOP_INTERPRETERS,
        **HTTP_INTERPRETERS,
    }
    compose = compositions[doeff_interpreter_name]

    def interpret(program: Program) -> object:
        return run(scheduled(compose(program)))

    return interpret
