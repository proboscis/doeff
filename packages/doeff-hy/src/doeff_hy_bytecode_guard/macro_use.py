# moved-submodule-shim: doeff_hy_bytecode_guard.macro_use -> doeff_hy_bytecode_guard._macro_use
# moved-submodule-shim-names doeff_hy_bytecode_guard.macro_use:
#   ANON_VAR_NAME ClosureDigest ClosureWalk CodeFacts CodeReadings CodeSlot ENVIRONMENT_READ
#   FACTS_TAG FactsRow FileState GENERATED_NAME_PREFIX GlobalName IMPORT_SCOPE ImportReference
#   ImportScope LOCAL_MACRO_TABLE MACRO_TABLE MacroRecord MacroRecording MacroReference NameUse
#   NameWay NamespaceRead NotImportable OwnCode READER_TABLE RecordingTable RememberedDigest
#   RowCheck Sha256Of UsedFile UsedMacro UsedMacroKey UsedValue ValueReference WholeRequire
#   current_scope exported_macro_names macro_digest record_is_current source_text value_digest
"""Moved: ``doeff_hy_bytecode_guard.macro_use`` is now ``doeff_hy_bytecode_guard._macro_use`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy_bytecode_guard.macro_use…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._macro_use import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy_bytecode_guard._macro_use", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy_bytecode_guard._macro_use")
