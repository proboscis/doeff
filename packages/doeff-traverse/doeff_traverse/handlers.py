# moved-submodule-shim: doeff_traverse.handlers -> doeff_traverse._handlers
# moved-submodule-shim-names doeff_traverse.handlers:
#   BarFactory Collection CollectionEffect Fail HistoryEntry Inspect ItemOutcome
#   ItemOutcomeNotPassedError ItemResult ProgressBar Reduce Skip SortBy Take Traverse Zip
#   fail_handler normalize_to_none parallel parallel_fail_fast progress_bars sequential
"""Moved: ``doeff_traverse.handlers`` is now ``doeff_traverse._handlers`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_traverse.handlers…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_traverse._handlers import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_traverse._handlers", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_traverse._handlers")
