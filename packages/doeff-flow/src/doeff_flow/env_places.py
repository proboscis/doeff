# moved-submodule-shim: doeff_flow.env_places -> doeff_flow._env_places
"""Moved: ``doeff_flow.env_places`` is now ``doeff_flow._env_places`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_flow.env_places…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_flow._env_places import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_flow._env_places", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_flow._env_places")
