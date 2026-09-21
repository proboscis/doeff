"""専用操作のProgramを同じeffect契約のdefhandlerで実行する。"""

import importlib

import pytest

from doeff import run

_deftests = importlib.import_module("sessionhost_cache_maintenance_deftests")
_names = [name for name in dir(_deftests) if name.startswith("test_")]
assert _names


@pytest.mark.parametrize("name", _names)
def test_cache_maintenance_program(name: str) -> None:
    getattr(_deftests, name)(run)
