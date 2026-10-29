# moved-submodule-shim: doeff_core_effects.cache -> doeff_core_effects._cache
# moved-submodule-shim-names doeff_core_effects.cache:
#   CACHE_PATH_ENV_KEY FrozenDict cache cache_1hour cache_1min cache_5min cache_forever
#   clear_persistent_cache default_cache_key persistent_cache_path
"""Moved: ``doeff_core_effects.cache`` is now ``doeff_core_effects._cache`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.cache…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._cache import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._cache", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._cache")
