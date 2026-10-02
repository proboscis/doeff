"""模擬の環境(doeff_cluster.sim)の下の検の実行環境 — 解釈器の口だけ(集め方は package の tests/ と同じ root の ini)。

- 検は Hy の ``test_*.hy``(deftest)。集め手は root の ini の ``doeff_hy_test_files``(doeff-adr の plugin の 1 点)— この conftest は集めない。
- deftest は handler を本体の中で被せる(入口の組み立てを模擬の組で回す)ので、``doeff_interpreter`` は scheduler だけを付けて 1 回回す。
- 走らせ方: ``uv run pytest packages/doeff-cluster/src/doeff_cluster/sim``(package の tests/ と別の session)。
- 置き場が package の根である理由: 検は doeff-linter の DOEFF136 のため sim の dir の下に在り、conftest は検の dir の祖先にしか効かない。
  一方 source(src/doeff_cluster)は pytest を import しない(tests/test_package_independence.hy の許可表)ので、src の外で祖先に当たる
  ここに置く。tests/ の下の検には tests/conftest.py の ``doeff_interpreter`` が近い方として勝つ。
"""

from collections.abc import Callable

import pytest
from doeff_core_effects.scheduler import scheduled

from doeff import Program, run


@pytest.fixture
def doeff_interpreter() -> Callable[[Program], object]:
    """deftest の Program を scheduler つきで 1 回回すため(handler の組は deftest の本体が選ぶ)。"""
    return lambda program: run(scheduled(program))
