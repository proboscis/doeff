"""Pytest wrapper for sessionhost SQLite store deftests (DOE-004 C3).

Same dynamic exposure pattern as ``test_sessionhost_policy.py``: every
``test_*`` deftest in ``sessionhost_store_deftests.hy`` is surfaced
automatically so a forgotten deftest cannot silently never run.
"""

import importlib
import sys
from pathlib import Path
from typing import Any

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

from doeff import run

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_store_deftests")


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    if env is not None:
        raise ValueError("sessionhost store deftests do not use env overrides")
    return run(program)


def _make_wrapper(deftest_fn: Any) -> Any:
    def _wrapper() -> None:
        deftest_fn(_deftest_interpreter)

    _wrapper.__name__ = deftest_fn.__name__
    _wrapper.__doc__ = deftest_fn.__doc__
    # deftest の :skip-if / :marks は deftest_fn.pytestmark に乗る — 写さないと包みが印を落とし、前提の無い宿で
    # skip されるはずの検が走って赤になる(zeus の herdr 不在で 8 本)。
    _wrapper.__dict__["pytestmark"] = list(getattr(deftest_fn, "pytestmark", []))
    return _wrapper


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_store_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = _make_wrapper(getattr(_deftests, _name))
