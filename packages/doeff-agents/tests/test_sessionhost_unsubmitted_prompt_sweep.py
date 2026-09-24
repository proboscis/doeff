"""Pytest wrapper for issue #568 deftests (ADR-DOE-AGENTS-010).

Every ``test_*`` deftest in ``sessionhost_unsubmitted_prompt_sweep_deftests.hy``
is exposed dynamically instead of hand-written one-by-one: a deftest added to
the Hy module but forgotten here would otherwise silently never run, which is
the exact failure mode an enforcement suite must not have.

deftest は包み直さず、そのまま公開する。包むと ``pytestmark``(skipif /
marks / parametrize)が関数の ``__dict__`` ごと落ち、書いた宣言が黙って
効かなくなる(ADR-DOE-HY-002 law deftest-params-are-honored:
``params_silently_dropped == 0``)。実行時の ``doeff_interpreter`` は
conftest.py の fixture が供給する(同 R3)。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_unsubmitted_prompt_sweep_deftests")


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_unsubmitted_prompt_sweep_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
