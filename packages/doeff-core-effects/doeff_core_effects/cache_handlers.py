# moved-submodule-shim: doeff_core_effects.cache_handlers -> doeff_core_effects._cache_handlers
# moved-submodule-shim-names doeff_core_effects.cache_handlers:
#   cache_handler content_address in_memory_cache_handler make_memo_rewriter memo_rewriters
#   sqlite_cache_handler
"""Moved: ``doeff_core_effects.cache_handlers`` is now ``doeff_core_effects._cache_handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.cache_handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._cache_handlers import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._cache_handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._cache_handlers")
