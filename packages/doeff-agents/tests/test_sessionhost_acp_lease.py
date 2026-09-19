"""Pytest wrapper for the lease-return deftests (card acp:kanban-issue:ki-f2747267e24d B2 / B3・ADR-DOE-AGENTS-012 R51).

Same dynamic exposure pattern as ``test_sessionhost_acp_profile.py``: every
``test_*`` deftest in ``sessionhost_acp_lease_deftests.hy`` is surfaced
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

_deftests = importlib.import_module("sessionhost_acp_lease_deftests")


def _deftest_interpreter(program: Any, *, env: dict[Any, Any] | None = None) -> Any:
    if env is not None:
        raise ValueError("sessionhost acp lease deftests do not use env overrides")
    return run(program)


def _make_wrapper(deftest_fn: Any) -> Any:
    def _wrapper() -> None:
        deftest_fn(_deftest_interpreter)

    _wrapper.__name__ = deftest_fn.__name__
    _wrapper.__doc__ = deftest_fn.__doc__
    return _wrapper


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_acp_lease_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = _make_wrapper(getattr(_deftests, _name))
