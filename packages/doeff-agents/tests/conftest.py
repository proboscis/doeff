"""doeff-agents テストの共有配線。

- Hy import hook を有効化する(``*_deftests.hy`` の import に必要)。
- deftest の実行時 interpreter fixture を供給する。
- Hy の ``test_*.hy`` の集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない(``*_deftests.hy`` は名が ``test_`` で始まらないので集まらず、従来どおり ``test_*.py`` が公開する)。

責務境界(ADR-DOE-HY-002 R2/R3): deftest params の受け渡しは doeff-hy が、
収集は doeff-adr の pytest plugin が所有し、**実行時 fixture は消費側**
(ここでは doeff-agents)が所有する。この fixture は docs/adr/conftest.py の
参照実装と同じ契約で、第 2 の定義点ではない。

各 ``test_sessionhost_*.py`` は deftest を**包み直さず**そのまま公開する。
包むと ``pytestmark``(skipif / marks / parametrize)が関数の ``__dict__``
ごと落ちるため(ADR-DOE-HY-002 law deftest-params-are-honored:
``params_silently_dropped == 0``)。
"""

import sys
from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401 — activates Hy import hook
import pytest

from doeff import Program

TESTS_DIR = Path(__file__).resolve().parent

# 契約テストの組み立ての module(driver_io_contract_handlers.hy・session_store_contract_handlers.hy)を名で import できるようにする
# (pytest は検の file の dir を遅れて足すので、他の testpaths と一緒に集めた時に備える)。
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


# 契約テストでない deftest の名: handler は各 deftest が本体の中で被せる。
PLAIN = "plain"


@pytest.fixture
def doeff_interpreter_name() -> str:
    return PLAIN


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[..., object]:
    """deftest を走らせる実行時 interpreter(ADR-DOE-HY-002 R3 の参照実装と同じ形)。

    ``:env`` は reader handler 経由で必ず反映する(黙って無視しない)。``:interpreters`` の名
    (契約テスト)は handler の組の組み立てを引いて被せる。名 → 組み立ての
    表は driver_io_contract_handlers.hy・session_store_contract_handlers.hy が持ち、表に無い名は
    KeyError で落とす(黙って素通しにしない — R2)。
    """
    from driver_io_contract_handlers import INTERPRETERS as DRIVER_IO_INTERPRETERS
    from session_store_contract_handlers import INTERPRETERS as SESSION_STORE_INTERPRETERS

    compositions: dict[str, Callable[[Program], Program]] = {
        PLAIN: lambda program: program,
        **DRIVER_IO_INTERPRETERS,
        **SESSION_STORE_INTERPRETERS,
    }
    compose = compositions[doeff_interpreter_name]

    def run_program(program: Program, *, env: dict[str, object] | None = None) -> object:
        from doeff import run

        if env:
            from doeff_core_effects.handlers import reader

            program = reader(dict(env))(program)
        return run(compose(program))

    return run_program
