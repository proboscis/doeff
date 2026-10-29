# moved-submodule-shim: doeff_hy_bytecode_guard.loader_hooks -> doeff_hy_bytecode_guard._loader_hooks
# moved-submodule-shim-names doeff_hy_bytecode_guard.loader_hooks:
#   ExecModule GetCode GetData HY_SOURCE_SUFFIXES IMPORT_SCOPE ImportScope SourceToCode
#   TYPE_CHECKING TYPE_CHECK_EXPANSION bytecode_is_current current_scope executed_source_is_current
#   file_sha256 gensym_renaming install installed is_hy_source loaded_record macro_recording
#   record_fingerprint record_from_json record_is_current_here record_to_json
#   source_to_code_as_import value_reference
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
