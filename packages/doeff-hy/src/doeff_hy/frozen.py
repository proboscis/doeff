# moved-submodule-shim: doeff_hy.frozen -> doeff_hy._frozen
# moved-submodule-shim-names doeff_hy.frozen:
#   FrozenJson FrozenMap JSON_LEAF_TYPES V deep_frozen_map freeze_json freeze_json_text
#   frozen_json_object frozen_map_of hyx_deeply_frozenXquestion_markX thaw_json
"""Moved: ``doeff_hy.frozen`` is now ``doeff_hy._frozen`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.frozen…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._frozen import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._frozen", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._frozen")
