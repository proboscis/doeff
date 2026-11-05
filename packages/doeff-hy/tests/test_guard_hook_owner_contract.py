"""The module that owns the hook the guard wraps around SourceFileLoader.get_code exports the two functions called by the bytecode precompile tool, with the same names and argument shapes.

The worker bake tool (doeff-cluster _foundation/bytecode_pool.hy) does not import the doeff of the root venv it prepares by module name --
it pulls the root guard from sys.modules as the module owning the get_code hook, and calls its bytecode_is_current and
source_to_code_as_import (ADR-DOE-BUILD-002 R7). The owner module name may change between versions (loader_hooks -> _loader_hooks), but
changing these two names or the argument shapes makes worker preparation fail on a new doeff root (2026-11-01 -- on a doeff root where the name location had moved,
preparation of an old worker failed with an import error). The source_to_code hook may be layered over the guard by the Hy importer (hy.importer), so
the tool checks the get_code owner first.
"""

from __future__ import annotations

import importlib.machinery
import inspect
import sys

from doeff_hy_bytecode_guard._loader_hooks import install

#: The functions the tool calls, and the sequence of their argument names.
CONTRACT = {
    "bytecode_is_current": ["path", "pyc", "source"],
    "source_to_code_as_import": ["loader", "data", "path"],
}


def test_the_module_owning_the_get_code_hook_offers_the_bake_tools_entries() -> None:
    install()
    owner = sys.modules[importlib.machinery.SourceFileLoader.get_code.__module__]
    for name, parameters in CONTRACT.items():
        entry = getattr(owner, name, None)
        assert callable(entry), (owner.__name__, name)
        assert list(inspect.signature(entry).parameters) == parameters, (name, inspect.signature(entry))
