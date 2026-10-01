"""doeff-records の検の実行環境(収集と解釈器の口だけ — 中身は interpreters.hy)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- 解釈器は 3 つ(``plain`` / ``memory`` / ``pg``)。``pg`` は env ``DOEFF_RECORDS_TEST_PG_DSN`` が無ければ skip する
  (psycopg は依存に無いので ``uv run --with psycopg`` で足す)。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest

from doeff import Program


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
