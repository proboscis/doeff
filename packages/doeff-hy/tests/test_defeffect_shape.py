"""Publishes the deftests of the type behavior of defeffect.

The deftests in ``defeffect_shape_deftests.hy`` are published as they are, without re-wrapping (ADR-DOE-HY-002 —
wrapping drops pytestmark). The runtime ``doeff_interpreter`` is supplied by this module's fixture.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # enables the Hy import hook

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_deftests = importlib.import_module("defeffect_shape_deftests")


@pytest.fixture
def doeff_interpreter() -> Callable[..., object]:
    """Run the deftest Program (env replacement is not used — it is an error if one is passed)."""
    from doeff import run

    def run_program(program: object, *, env: object = None) -> object:
        if env:
            raise NotImplementedError("the defeffect checks do not use :env")
        return run(program)

    return run_program


_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names, "defeffect_shape_deftests exposes no test_* deftests"
for _name in _names:
    globals()[_name] = getattr(_deftests, _name)
