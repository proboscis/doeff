# moved-submodule-shim: doeff_adr.pytest_plugin -> doeff_adr._pytest_plugin
# moved-submodule-shim-names doeff_adr.pytest_plugin:
#   DEFAULT_FILE_PATTERNS DEFAULT_WIRING_MAX_DIRS DependencyChecks DoeffAdrHookspecs DoeffAdrHyFile
#   ExpansionUnknown HyFileMatcher HyScriptItem HySourceFinder HyTestFile HyTestSkip HyTestSkips
#   IGNORED_DISCOVERY_DIRECTORIES Indexed IndexedFunctions NeedsImport NotDefaultScope PendingEntry
#   RecordMismatch SessionPaths WIRING_MODES WiringMode WiringUncollected WiringVerdict
#   WiringVerified WiringWalkAborted WiringWalkBudgetError check_no_unrecorded_items
#   default_scope_wiring expansion_of forget_cached import_module_for items_cache_dir
#   items_cache_dir_for parse_hy_test_skips pending_entry plan_collection pytest_addhooks
#   pytest_addoption pytest_collect_file pytest_collection_finish pytest_collection_modifyitems
#   pytest_configure pytest_doeff_import_hy_module pytest_report_collectionfinish
#   pytest_runtest_setup pytest_unconfigure session_paths session_wiring stub_module
#   supports_indexed_collection swap_in_real_function swap_in_real_module_marks verify_records
#   wiring_failure_message write_cached
"""Moved:``doeff_adr.pytest_plugin`` is now ``doeff_adr._pytest_plugin`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.pytest_plugin…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._pytest_plugin import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._pytest_plugin", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._pytest_plugin")
