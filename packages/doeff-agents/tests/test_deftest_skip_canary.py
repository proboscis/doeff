"""deftest の skip が pytest に届いていることの番犬(挙動の検・ADR-DOE-HY-002)。

``deftest_skip_canary_deftests.hy`` の 2 本を、本番の公開 file と同じ形(包まず
そのまま公開)で pytest に出す。期待は常に ``1 skipped, 1 passed``:

- ``test_declared_skip_is_honored``: 必ず真の ``:skip-if`` + 必ず失敗する本体。
  マークが届いていれば skipped、落ちていれば本体が走って failed。
- ``test_unskipped_companion_actually_runs``: 走るべき対照。skip されたら番犬が
  空振りしている。

deftest は包み直さず、そのまま公開する。包むと ``pytestmark``(skipif /
marks / parametrize)が関数の ``__dict__`` ごと落ち、書いた宣言が黙って
効かなくなる(law deftest-params-are-honored: ``params_silently_dropped == 0``)。
実行時の ``doeff_interpreter`` は conftest.py の fixture が供給する(同 R3)。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("deftest_skip_canary_deftests")


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "deftest_skip_canary_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
