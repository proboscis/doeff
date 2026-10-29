# moved-submodule-shim: doeff_hy.static_check -> doeff_hy._static_check
# moved-submodule-shim-names doeff_hy.static_check:
#   BOOKKEEPING_MODULES BaselineUnreadable BundleRow CARRY_SEMANTICS CacheMiss CachedFinding
#   CachedProjection CachedSpan CarriedRow CarryRecord CarryUnreadableError Checked Closure
#   CompileFailure DEFAULT_REFRESH_RUNS DEFEFFECT_OUTCOMES_MODULE Diagnostic EXPANDER_LIMIT
#   ExpansionWorkerLostError FileInputs FileRows FileStat Finding HyPosition InputGraph ModuleScope
#   POST_PROCESSING PYRIGHT_THREADS PYRIGHT_THREADS_ENV Projection STATIC_HELPER_IMPORTS SharedScope
#   SlotExpired Span SpanIndex Split baseline_json bundle_key bundle_signature carry_text
#   collect_findings conversation_setting default_cache_dir errors_of expander_limit gensym_renaming
#   hy_files import_roots imported_modules in_root is_alias_import load load_bundle locate
#   macro_recording main module_name next_record own_digest place places_of plan_carry
#   probe_environment project project_cached project_closure pyright_settings pyright_threads
#   pyright_threads_setting python_without_bytecode read_baseline read_carry record_is_current_here
#   row_is_current run_carried run_pyright search_dirs semantics_digest settle slot_label span_index
#   split stat_of static_view store store_bundle take_slot thread_clamp_note value_reference
#   with_canonical_gensyms with_module_level_imports with_static_helpers without_bookkeeping
#   write_carry
"""Moved: ``doeff_hy.static_check`` is now ``doeff_hy._static_check`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.static_check…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._static_check import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._static_check", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._static_check")
