"""doeff-cluster の検の実行環境。

- 検は Hy の ``test_*.hy``(deftest)。doeff-adr の Hy の file の収集(``DoeffAdrHyFile``)をこの dir の中だけで使う。
  repo の根の ini の ``doeff_adr_hy_files`` には足さない — 足すと根の母集団の配線の検め(executable ADR の在処)が
  この package の検を「根で集めていない ADR」と数える(package の母集団は ``make test-packages`` が別に走らせる)。
- module 名は ``tests.<名>``(``tests/__init__.py`` の親 = この package の dir からの名 — doeff-adr の
  ``_import_base_for_path``)。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from doeff_adr.pytest_plugin import DoeffAdrHyFile
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture
def doeff_interpreter() -> Callable[[Program], object]:
    def interpret(program: Program) -> object:
        return run(scheduled(program))

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
