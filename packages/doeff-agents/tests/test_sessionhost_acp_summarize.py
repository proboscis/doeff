"""Pytest wrapper for the agentd summarize deftests (ADR-DOE-AGENTS-012 R37・段 12 lane 12j・agora-redesign #233).

Same dynamic exposure pattern as ``test_sessionhost_acp_verify.py``: every
``test_*`` deftest in ``sessionhost_acp_summarize_deftests.hy`` is surfaced
automatically so a forgotten deftest cannot silently never run.
"""

import importlib
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # registers Hy import hooks for deftest modules

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("sessionhost_acp_summarize_deftests")




_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "sessionhost_acp_summarize_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
