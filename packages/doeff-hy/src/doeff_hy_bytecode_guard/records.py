# moved-submodule-shim: doeff_hy_bytecode_guard.records -> doeff_hy_bytecode_guard._records
# moved-submodule-shim-names doeff_hy_bytecode_guard.records:
#   ANON_VAR_NAME COVERED ENVIRONMENT_READ EXPORT_NAMES FLAG_CHECK_SOURCE FLAG_HASH_BASED
#   GENERATED_NAME_PREFIX GENSYM_NAME LOCAL_MACRO_TABLE MACRO_TABLE MacroRecord PYC_HEADER_BYTES
#   READER_TABLE RECORD_TAG RENAMED_BINDING RecordParts UsedFile UsedMacro UsedValue ValueReference
#   WholeRequire canonical_gensyms exported_macro_names fold_anon_var_numbers gensym_renames
#   macro_tables pyc_bytes python_checks_source record_fingerprint record_from_json
#   record_from_parts record_of record_parts record_to_json renamed timestamp_header_matches
#   unread_gensym_texts with_record
"""Moved: ``doeff_hy_bytecode_guard.records`` is now ``doeff_hy_bytecode_guard._records`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy_bytecode_guard.records…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._records import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy_bytecode_guard._records", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy_bytecode_guard._records")
