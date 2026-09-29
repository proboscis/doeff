"""doeff-cluster の検の実行環境。

- 検は Hy の ``test_*.hy``(deftest)。doeff-adr の Hy の file の収集(``DoeffAdrHyFile``)をこの dir の中だけで使う。
  repo の根の ini の ``doeff_adr_hy_files`` には足さない — 足すと根の母集団の配線の検め(executable ADR の在処)が
  この package の検を「根で集めていない ADR」と数える(package の母集団は ``make test-packages`` が別に走らせる)。
- module 名は ``tests.<名>``(``tests/__init__.py`` の親 = この package の dir からの名 — doeff-adr の
  ``_import_base_for_path``)。
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

# doeff-effect-analyzer の Python の front end(foundation_check が使う開発の道具 — doeff-cluster の実行時の依存ではない)。
# maturin の混ぜた project で、front end は Rust の拡張なしで読めるので、入っていなければ python/ を import の路に足す
# (packages/doeff-effect-analyzer/tests/python/conftest.py と同じ扱い)。
if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "doeff-effect-analyzer" / "python"))

import pytest
from doeff_adr.pytest_plugin import DoeffAdrHyFile
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None


# 契約テストでない deftest の名: handler は各 deftest が本体の中で被せる。
PLAIN = "plain"


@pytest.fixture
def doeff_interpreter_name() -> str:
    return PLAIN


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    """deftest の Program を、:interpreters の名の handler の組の下で scheduler つきで 1 回回す。

    契約テストは deftest の ``:interpreters`` で handler を差し替える。名 → 組み立ての表は
    coordinator_contract_handlers.hy・host_reads_contract_handlers.hy・runtime_facts_contract_handlers.hy・
    file_contract_handlers.hy・tree_contract_handlers.hy が持ち、
    ここはその表を引くだけ(名の無い deftest は今までどおり素のまま回す)。
    """
    if doeff_interpreter_name == PLAIN:
        compose: Callable[[Program], Program] = lambda program: program
    else:
        import hy  # noqa: F401  - Hy の module を import できるようにする

        from tests.coordinator_contract_handlers import INTERPRETERS as COORDINATOR_INTERPRETERS
        from tests.file_contract_handlers import INTERPRETERS as FILE_INTERPRETERS
        from tests.host_reads_contract_handlers import INTERPRETERS as HOST_READS_INTERPRETERS
        from tests.runtime_facts_contract_handlers import INTERPRETERS as RUNTIME_FACTS_INTERPRETERS
        from tests.tree_contract_handlers import INTERPRETERS as TREE_INTERPRETERS

        compositions: dict[str, Callable[[Program], Program]] = {
            **COORDINATOR_INTERPRETERS,
            **FILE_INTERPRETERS,
            **HOST_READS_INTERPRETERS,
            **RUNTIME_FACTS_INTERPRETERS,
            **TREE_INTERPRETERS,
        }
        compose = compositions[doeff_interpreter_name]

    def interpret(program: Program) -> object:
        return run(scheduled(compose(program)))

    return interpret


@pytest.fixture(scope="session")
def served_coordinator(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """本物の coordinator の process 1 つを検の間で共有する(test_detached.hy の served の組・起動に数秒かかる)。

    scope は session: Hy の file の検は Module の node の下に無いので、module の scope を取れない。
    """
    import hy  # noqa: F401  - Hy の module を import できるようにする

    from tests.served_fixtures import start_coordinator

    url, process = start_coordinator(tmp_path_factory.mktemp("served"))
    yield url
    process.terminate()
    process.wait(timeout=30)
