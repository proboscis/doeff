"""doeff-records の検の実行環境(収集と解釈器の口だけ — 中身は interpreters.hy)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 解釈器は 3 つ(``plain`` / ``memory`` / ``pg``)。``pg`` は env ``DOEFF_RECORDS_TEST_PG_DSN`` の PostgreSQL に載る
  (psycopg は依存に無いので ``uv run --with psycopg`` で足す)。
- env が無ければ、pytest_configure(検の module の import より前)で使い捨ての PostgreSQL を立てて env を置く(agora-redesign #2830)。
  部品は doeff-core-effects の tests の postgres_support/disposable_postgres.py の 1 か所(この package が SQL の effect の
  PostgreSQL の答え手を借りる先)。用意できない機体では、検は「使い捨ての PostgreSQL を用意できない: <理由>」で skip する。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff import Program

# 使い捨ての PostgreSQL の部品(置き場は doeff-core-effects の tests の 1 か所 — コピーを持たない)。
POSTGRES_SUPPORT_DIR = (
    Path(__file__).resolve().parents[2] / "doeff-core-effects" / "tests" / "postgres_support"
)
if str(POSTGRES_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(POSTGRES_SUPPORT_DIR))

from disposable_postgres import provide_session_postgres  # noqa: E402 - sys.path の後


def pytest_configure(config: pytest.Config) -> None:
    """検の module が DSN の env を読む前に、使い捨ての PostgreSQL を用意する(env が在ればそれを使う)。"""
    provide_session_postgres(config)


@pytest.fixture
def doeff_interpreter_name() -> str:
    return "plain"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Iterator[Callable[[Program], object]]:
    from tests.interpreters import build_interpreter, skip_reason

    reason = skip_reason(doeff_interpreter_name)
    if reason:
        pytest.skip(reason)
    built = build_interpreter(doeff_interpreter_name)
    try:
        yield built.run
    finally:
        built.close()
