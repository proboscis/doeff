"""模擬の環境(doeff_cluster.sim)の下の検の実行環境 — 解釈器の口だけ(集め方は package の tests/ と同じ root の ini)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- deftest は handler を本体の中で被せる(入口の組み立てを模擬の組で回す)ので、``doeff_interpreter`` は scheduler だけを付けて 1 回回す。
- 走らせ方: ``uv run pytest packages/doeff-cluster/src/doeff_cluster/sim``(package の tests/ と別の session)。
"""

from collections.abc import Callable

import pytest
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run


@pytest.fixture
def doeff_interpreter() -> Callable[[Program], object]:
    """deftest の Program を scheduler つきで 1 回回すため(handler の組は deftest の本体が選ぶ)。"""
    return lambda program: run(scheduled(program))
