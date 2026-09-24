"""Pytest wrapper for the live-tail poll cadence deftests (段 8 lane 4aa・agora-redesign #63).

Same dynamic exposure pattern as ``test_sessionhost_acp_profile.py``: every
``test_*`` deftest in ``sessionhost_acp_live_poll_deftests.hy`` is surfaced
automatically so a forgotten deftest cannot silently never run.

deftest は包み直さず、そのまま公開する。包むと ``pytestmark``(skipif /
marks / parametrize)が関数の ``__dict__`` ごと落ち、書いた宣言が黙って
効かなくなる(ADR-DOE-HY-002 law deftest-params-are-honored:
``params_silently_dropped == 0``)。実行時の ``doeff_interpreter`` は
conftest.py の fixture が供給する(同 R3)。
"""

import importlib
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_acp_live_poll_deftests")


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_acp_live_poll_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
