# moved-submodule-shim: doeff_hy_bytecode_guard.code_store -> doeff_hy_bytecode_guard._code_store
# moved-submodule-shim-names doeff_hy_bytecode_guard.code_store:
#   BINDING_SUFFIX CODE_ENTRIES_PER_KEY CODE_SUFFIX CODE_TAG FACTS_SUFFIX HY_SOURCE_SUFFIXES
#   IMPORTS_SUFFIX NEW_ENTRY_FLOOR_SECONDS PYC_SUFFIX STORE_ENV StoredCode TREE_ENTRIES_PER_KEY
#   TREE_SUFFIX add_code add_tree code_entries code_fingerprint code_key content_key current_code
#   current_entry discard_entry entry_path entry_pyc link_entry_pyc link_pyc read_entry store_dir
#   store_is_named stored_code touch_entry tree_entries write_entry
"""Moved: ``doeff_hy_bytecode_guard.code_store`` is now ``doeff_hy_bytecode_guard._code_store`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy_bytecode_guard.code_store…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._code_store import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy_bytecode_guard._code_store", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy_bytecode_guard._code_store")
