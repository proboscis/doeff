# moved-submodule-shim: doeff_core_effects.memo_handlers -> doeff_core_effects._memo_handlers
# moved-submodule-shim-names doeff_core_effects.memo_handlers:
#   DurableStorage InMemoryStorage MemoExists MemoGet MemoKeyFn MemoPut MemoPutEffect RecomputeCost
#   SQLiteStorage content_address in_memory_memo_handler json_gzip_deserialize json_gzip_serialize
#   make_memo_rewriter memo_handler memo_rewriters sqlite_memo_handler
"""Moved: ``doeff_core_effects.memo_handlers`` is now ``doeff_core_effects._memo_handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.memo_handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._memo_handlers import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._memo_handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._memo_handlers")
