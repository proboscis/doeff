# moved-submodule-shim: doeff_hy_bytecode_guard.import_scope -> doeff_hy_bytecode_guard._import_scope
# moved-submodule-shim-names doeff_hy_bytecode_guard.import_scope:
#   IMPORT_SCOPE ImportScope SharedScope current_scope
"""Moved: ``doeff_hy_bytecode_guard.import_scope`` is now ``doeff_hy_bytecode_guard._import_scope`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy_bytecode_guard.import_scope…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._import_scope import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy_bytecode_guard._import_scope", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy_bytecode_guard._import_scope")
