"""模擬の環境(doeff_cluster.sim)の下の検の実行環境 — package の tests/conftest.py と同じ集め方(agora-redesign #2542)。

- 検は Hy の ``test_*.hy``(deftest)。doeff-adr の Hy の file の収集(``DoeffAdrHyFile``)をこの dir の中だけで使う。
- deftest は handler を本体の中で被せる(入口の組み立てを模擬の組で回す)ので、``doeff_interpreter`` は scheduler だけを付けて 1 回回す。
- 走らせ方: ``uv run pytest packages/doeff-cluster/src/doeff_cluster/sim``(package の tests/ と別の session)。
"""

from collections.abc import Callable
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
    """deftest の Program を scheduler つきで 1 回回すため(handler の組は deftest の本体が選ぶ)。"""
    return lambda program: run(scheduled(program))
