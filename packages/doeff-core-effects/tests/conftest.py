"""Test execution environment for doeff-core-effects (collection of Hy ``test_*.hy`` files and the deftest interpreters).

- Hy ``test_*.hy`` files are collected by the root ini's ``doeff_hy_test_files`` (a single point of the doeff-adr plugin) -- this conftest does not collect them (the package population is run separately by ``make test-packages``).
- Contract tests swap handlers through deftest's ``:interpreters``. The name -> assembly table is
  stop_contract_handlers.hy, http_contract_handlers.hy, process_contract_handlers.hy, http_server_contract_handlers.hy,
  meter_contract_handlers.hy, latest_contract_handlers.hy, heap_contract_handlers.hy,
  stack_dump_contract_handlers.hy, warm_contract_handlers.hy (the effects of the awaited child) and the others; this file only
  looks up that table and runs once with a scheduler. Interpreters that need an outside module (REQUIRES) are skipped in environments without that module.
- The DSN env for the real PostgreSQL tests (test_sql_effects.hy) is prepared in pytest_configure (before the test modules are imported)
  by postgres_support/disposable_postgres.py -- if the env is absent, a disposable PostgreSQL is started
  (the conftest of the doeff-records tests calls the same component).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - lets the Hy composition modules (*_contract_handlers.hy) be imported
import pytest
from doeff_core_effects import scheduled

from doeff import Program, run

TESTS_DIR = Path(__file__).resolve().parent

# TESTS_DIR makes the uniquely-named composition modules importable even when this suite is collected together
# with other testpaths (pytest only prepends a test file's own directory lazily).
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# Component for the disposable PostgreSQL (the conftest of the doeff-records tests reads the same dir -- one location).
POSTGRES_SUPPORT_DIR = TESTS_DIR / "postgres_support"
if str(POSTGRES_SUPPORT_DIR) not in sys.path:
    sys.path.insert(0, str(POSTGRES_SUPPORT_DIR))

from disposable_postgres import provide_session_postgres  # noqa: E402 - after sys.path


def pytest_configure(config: pytest.Config) -> None:
    """Prepare a disposable PostgreSQL before the test modules read the DSN env (use the env if it exists)."""
    provide_session_postgres(config)


# Names of deftests that are not contract tests: each deftest wraps the handlers inside its own body (test_sql_effects.hy).
PLAIN = "plain"


@pytest.fixture
def doeff_interpreter_name() -> str:
    return PLAIN


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    """Run the deftest Program once with a scheduler under the handler set of the :interpreters name."""
    from heap_contract_handlers import INTERPRETERS as HEAP_INTERPRETERS
    from http_contract_handlers import INTERPRETERS as HTTP_INTERPRETERS
    from http_server_contract_handlers import INTERPRETERS as HTTP_SERVER_INTERPRETERS
    from http_server_contract_handlers import REQUIRES as HTTP_SERVER_REQUIRES
    from latest_contract_handlers import INTERPRETERS as LATEST_INTERPRETERS
    from meter_contract_handlers import INTERPRETERS as METER_INTERPRETERS
    from proc_table_contract_handlers import INTERPRETERS as PROC_TABLE_INTERPRETERS
    from process_contract_handlers import INTERPRETERS as PROCESS_INTERPRETERS
    from random_contract_handlers import INTERPRETERS as RANDOM_INTERPRETERS
    from stack_dump_contract_handlers import INTERPRETERS as STACK_DUMP_INTERPRETERS
    from stop_contract_handlers import INTERPRETERS as STOP_INTERPRETERS
    from warm_contract_handlers import INTERPRETERS as WARM_INTERPRETERS
    from ws_client_contract_handlers import INTERPRETERS as WS_CLIENT_INTERPRETERS
    from ws_client_contract_handlers import REQUIRES as WS_CLIENT_REQUIRES

    compositions: dict[str, Callable[[Program], Program]] = {
        PLAIN: lambda program: program,
        **STOP_INTERPRETERS,
        **HTTP_INTERPRETERS,
        **PROCESS_INTERPRETERS,
        **PROC_TABLE_INTERPRETERS,
        **HTTP_SERVER_INTERPRETERS,
        **METER_INTERPRETERS,
        **LATEST_INTERPRETERS,
        **HEAP_INTERPRETERS,
        **RANDOM_INTERPRETERS,
        **STACK_DUMP_INTERPRETERS,
        **WARM_INTERPRETERS,
        **WS_CLIENT_INTERPRETERS,
    }
    required = {**HTTP_SERVER_REQUIRES, **WS_CLIENT_REQUIRES}.get(doeff_interpreter_name)
    if required is not None and importlib.util.find_spec(required) is None:
        pytest.skip(f"interpreter {doeff_interpreter_name} requires {required}")
    compose = compositions[doeff_interpreter_name]

    def interpret(program: Program) -> object:
        return run(scheduled(compose(program)))

    return interpret
