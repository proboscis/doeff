"""Pytest wrapper for sessionhost shared launch program deftests (DOE-004 C2).

Same dynamic exposure pattern as ``test_sessionhost_policy.py``: every
``test_*`` deftest in ``sessionhost_launch_deftests.hy`` is surfaced
automatically so a forgotten deftest cannot silently never run.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

from doeff import run

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_launch_deftests")


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    if env is not None:
        raise ValueError("sessionhost launch deftests do not use env overrides")
    return run(program)


def _make_wrapper(deftest_fn: Any) -> Any:
    def _wrapper() -> None:
        deftest_fn(_deftest_interpreter)

    _wrapper.__name__ = deftest_fn.__name__
    _wrapper.__doc__ = deftest_fn.__doc__
    return _wrapper


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_launch_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = _make_wrapper(getattr(_deftests, _name))
