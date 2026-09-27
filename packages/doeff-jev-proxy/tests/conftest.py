"""doeff-jev-proxy の検の実行環境(収集と解釈器の口だけ — 検の世界は world.hy)。

- 検は Hy の ``test_*.hy``(deftest)。doeff-adr の Hy の file の収集(``DoeffAdrHyFile``)をこの dir の中だけで使う
  (doeff-records の tests/conftest.py と同じ形)。
- 解釈器は 1 つ(``plain`` = scheduler・await・try だけ)。代理の答え手の組は検の中で world.hy が組む(置き場は検ごとの一時の file・
  本物の Jev は台本の答え手)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import hy  # noqa: F401  - Hy の module を import できるようにする
import pytest
from doeff_adr.pytest_plugin import DoeffAdrHyFile
from doeff_core_effects import await_handler, try_handler
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run, with_handlers


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix == ".hy" and file_path.name.startswith("test_"):
        return DoeffAdrHyFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture
def doeff_interpreter_name() -> str:
    return "plain"


@pytest.fixture
def doeff_interpreter(doeff_interpreter_name: str) -> Callable[[Program], object]:
    def run_plain(program: Program) -> object:
        return run(scheduled(with_handlers([await_handler(), try_handler], program)))

    return run_plain
