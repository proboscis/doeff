# moved-submodule-shim: doeff_agents.session_store_sql -> doeff_agents._session_store_sql
"""Moved: ``doeff_agents.session_store_sql`` is now ``doeff_agents._session_store_sql`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_agents.session_store_sql…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

import hy  # noqa: F401  # the moved module is Hy: load Hy's import hook first

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_agents._session_store_sql", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_agents._session_store_sql")
