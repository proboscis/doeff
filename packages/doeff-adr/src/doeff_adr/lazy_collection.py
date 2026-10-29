# moved-submodule-shim: doeff_adr.lazy_collection -> doeff_adr._lazy_collection
# moved-submodule-shim-names doeff_adr.lazy_collection:
#   CacheHit CacheMiss CollectionPlan DependencyChecks FixtureRecord Indexed MalformedCacheEntry
#   NeedsImport OpaqueParam RecordMismatch RecordMismatchError RecordedModule ResolvedRecords
#   SignatureFunction UnrecordableError Verified XUNIT_NAMES check_no_unrecorded_items fixture_scope
#   import_module_for parametrize_record plan_collection read_cached runtime_function_record
#   stub_module swap_in_real_function swap_in_real_module_marks verify_records
"""Moved:``doeff_adr.lazy_collection`` is now ``doeff_adr._lazy_collection`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.lazy_collection…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._lazy_collection import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._lazy_collection", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._lazy_collection")
