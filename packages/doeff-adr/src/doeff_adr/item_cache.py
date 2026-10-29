# moved-submodule-shim: doeff_adr.item_cache -> doeff_adr._item_cache
# moved-submodule-shim-names doeff_adr.item_cache:
#   CACHE_FORMAT CacheEntry CacheHit CacheLookup CacheMiss DependencyChecks ExpansionUnknown
#   FixtureRecord FixtureScope ITEMS_CACHE_ENV MalformedCacheEntry MalformedCacheEntryError
#   PendingEntry ProviderFound ProviderMissing ProviderState RECORDING_RUNTIME RuntimeDependency
#   SourceDependency SourceSnapshot TYPE_CHECKING environment_value expansion_of fixture_scope
#   forget_cached items_cache_dir_for loaded_origins pending_entry read_cached write_cached
"""Moved:``doeff_adr.item_cache`` is now ``doeff_adr._item_cache`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.item_cache…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._item_cache import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._item_cache", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._item_cache")
