# moved-submodule-shim: doeff_core_effects.effects -> doeff_core_effects._effects
# moved-submodule-shim-names doeff_core_effects.effects:
#   Absent Ask Await Err Get HttpError HttpRequest HttpResponse Listen Local MissingEnvKeyError Ok
#   Program Put Raise ReadWriterLog Resumption Slog SlogEffect Tell Try WriterTellEffect
#   extra_needed resumption_of runs_carried_of slog
"""Moved: ``doeff_core_effects.effects`` is now ``doeff_core_effects._effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_core_effects.effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_core_effects._effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_core_effects._effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_core_effects._effects")
