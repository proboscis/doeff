# moved-submodule-shim: doeff_hy_bytecode_guard.loader_hooks -> doeff_hy_bytecode_guard._loader_hooks
"""Moved: ``doeff_hy_bytecode_guard.loader_hooks`` is now ``doeff_hy_bytecode_guard._loader_hooks`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy_bytecode_guard.loader_hooks…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._loader_hooks import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy_bytecode_guard._loader_hooks", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy_bytecode_guard._loader_hooks")
