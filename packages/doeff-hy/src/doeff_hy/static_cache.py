# moved-submodule-shim: doeff_hy.static_cache -> doeff_hy._static_cache
# moved-submodule-shim-names doeff_hy.static_cache:
#   BUNDLE_VERSION BundleRow CACHE_VERSION CacheMiss CachedFinding CachedProjection CachedSpan
#   FileStat LazySpans RACY_NS Span bundle_key bundle_signature default_cache_dir load load_bundle
#   place record_fingerprint record_from_json record_is_current_here record_to_json row_is_current
#   stat_of store store_bundle
"""Moved: ``doeff_hy.static_cache`` is now ``doeff_hy._static_cache`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.static_cache…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._static_cache import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._static_cache", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._static_cache")
