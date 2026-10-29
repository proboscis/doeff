# moved-submodule-shim: doeff_hy.static_stub -> doeff_hy._static_stub
# moved-submodule-shim-names doeff_hy.static_stub:
#   CompileFailure GENERATED_NAME_PREFIX MARK Stale StubText StubUnmade UsedModule generated
#   hidden_name hy_files import_roots main project pyright_settings stale_in stale_stubs
#   strict_errors stub_of unknown_in_users users_probe
"""Moved: ``doeff_hy.static_stub`` is now ``doeff_hy._static_stub`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.static_stub…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._static_stub import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._static_stub", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._static_stub")
