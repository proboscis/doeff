# moved-submodule-shim: doeff_core_effects.unhandled_hints -> doeff_core_effects._unhandled_hints
# moved-submodule-shim-names doeff_core_effects.unhandled_hints:
#   AcquireSemaphore Ask Await Cancel CompletePromise CreateExternalPromise CreatePromise
#   CreateSemaphore Discard FailPromise Gather Get Listen Local Put Race ReadWriterLog
#   ReleaseSemaphore SlogEffect Spawn Try UNHANDLED_EFFECT_HINTS UnhandledEffectHint Wait
#   WriterTellEffect hint_for
"""Moved: ``doeff_core_effects.unhandled_hints`` is now ``doeff_core_effects._unhandled_hints`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.unhandled_hints…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._unhandled_hints import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._unhandled_hints", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._unhandled_hints")
