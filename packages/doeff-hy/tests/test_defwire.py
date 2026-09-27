"""defwire と解き手(parse / dump)の deftest を公開する(agora-redesign #840)。

``defwire_deftests.hy`` の deftest を包み直さずそのまま公開する(ADR-DOE-HY-002 —
包むと pytestmark が落ちる)。実行時の ``doeff_interpreter`` はこの module の fixture が供給する。
"""

import importlib
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("defwire_deftests")


@pytest.fixture
def doeff_interpreter() -> Callable[..., object]:
    """deftest の Program を走らせる(env の差し替えは使わない — 渡されたら誤り)。"""
    from doeff import run

    def run_program(program: object, *, env: object = None) -> object:
        if env:
            raise NotImplementedError("defwire の検は :env を使わない")
        return run(program)

    return run_program


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "defwire_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
