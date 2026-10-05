"""doeff-core-effects の検の実行環境(Hy の ``test_*.hy`` の収集と deftest の解釈器)。

- Hy の ``test_*.hy`` の集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない(package の母集団は ``make test-packages`` が別に走らせる)。
- 契約テスト(agora-redesign #1159)は deftest の ``:interpreters`` で handler を差し替える。名 → 組み立ての表は
  stop_contract_handlers.hy・http_contract_handlers.hy・process_contract_handlers.hy・http_server_contract_handlers.hy・
  meter_contract_handlers.hy・latest_contract_handlers.hy・heap_contract_handlers.hy(agora-redesign #1440)・
  stack_dump_contract_handlers.hy(agora-redesign #2748)・warm_contract_handlers.hy(待ちの子の効果)が持ち、ここはその表を
  引いて scheduler つきで 1 回回すだけ。外の module が要る解釈器(REQUIRES)は、その module の無い環境では skip する。
- 実 PostgreSQL の検(test_sql_effects.hy)の DSN の env は、pytest_configure(検の module の import より前)で
  postgres_support/disposable_postgres.py が用意する — env が無ければ使い捨ての PostgreSQL を立てる(agora-redesign #2830・
  doeff-records の tests の conftest も同じ部品を呼ぶ)。
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - lets the Hy composition modules (*_contract_handlers.hy) be imported
import pytest
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run

TESTS_DIR = Path(__file__).resolve().parent

# TESTS_DIR makes the uniquely-named composition modules importable even when this suite is collected together
# with other testpaths (pytest only prepends a test file's own directory lazily).
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# 使い捨ての PostgreSQL の部品(doeff-records の tests の conftest も同じ dir を読む — 置き場は 1 か所)。
POSTGRES_SUPPORT_DIR = TESTS_DIR / "postgres_support"
if str(POSTGRES_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(POSTGRES_SUPPORT_DIR))

from disposable_postgres import provide_session_postgres  # noqa: E402 - sys.path の後


def pytest_configure(config: pytest.Config) -> None:
    """検の module が DSN の env を読む前に、使い捨ての PostgreSQL を用意する(env が在ればそれを使う)。"""
    provide_session_postgres(config)


# 契約テストでない deftest の名: handler は各 deftest が本体の中で被せる(test_sql_effects.hy)。
PLAIN = "plain"


@pytest.fixture
def doeff_interpreter_name() -> str:
    return PLAIN


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    """deftest の Program を、:interpreters の名の handler の組の下で scheduler つきで 1 回回す。"""
    from heap_contract_handlers import INTERPRETERS as HEAP_INTERPRETERS
    from http_contract_handlers import INTERPRETERS as HTTP_INTERPRETERS
    from http_server_contract_handlers import INTERPRETERS as HTTP_SERVER_INTERPRETERS
    from http_server_contract_handlers import REQUIRES as HTTP_SERVER_REQUIRES
    from latest_contract_handlers import INTERPRETERS as LATEST_INTERPRETERS
    from meter_contract_handlers import INTERPRETERS as METER_INTERPRETERS
    from process_contract_handlers import INTERPRETERS as PROCESS_INTERPRETERS
    from random_contract_handlers import INTERPRETERS as RANDOM_INTERPRETERS
    from stack_dump_contract_handlers import INTERPRETERS as STACK_DUMP_INTERPRETERS
    from stop_contract_handlers import INTERPRETERS as STOP_INTERPRETERS
    from warm_contract_handlers import INTERPRETERS as WARM_INTERPRETERS

    compositions: dict[str, Callable[[Program], Program]] = {
        PLAIN: lambda program: program,
        **STOP_INTERPRETERS,
        **HTTP_INTERPRETERS,
        **PROCESS_INTERPRETERS,
        **HTTP_SERVER_INTERPRETERS,
        **METER_INTERPRETERS,
        **LATEST_INTERPRETERS,
        **HEAP_INTERPRETERS,
        **RANDOM_INTERPRETERS,
        **STACK_DUMP_INTERPRETERS,
        **WARM_INTERPRETERS,
    }
    required = HTTP_SERVER_REQUIRES.get(doeff_interpreter_name)
    if required is not None and importlib.util.find_spec(required) is None:
        pytest.skip(f"解釈器 {doeff_interpreter_name} は {required} が要る")
    compose = compositions[doeff_interpreter_name]

    def interpret(program: Program) -> object:
        return run(scheduled(compose(program)))

    return interpret
