"""Pytest wrapper for sessionhost host-layer deftests (DOE-004 C3).

Same dynamic exposure pattern as ``test_sessionhost_policy.py``: every
``test_*`` deftest in ``sessionhost_host_deftests.hy`` is surfaced
automatically so a forgotten deftest cannot silently never run.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_host_deftests")




_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_host_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
