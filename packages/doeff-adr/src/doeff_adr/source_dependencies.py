# moved-submodule-shim: doeff_adr.source_dependencies -> doeff_adr._source_dependencies
# moved-submodule-shim-names doeff_adr.source_dependencies:
#   DependencyChecks LoadedSource ProviderFound ProviderMissing ProviderState RACY_WINDOW_NS
#   SourceDependency SourceSnapshot loaded_origins local_source snapshot stat_is_trusted
"""Moved:``doeff_adr.source_dependencies`` is now ``doeff_adr._source_dependencies`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_adr.source_dependencies…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_adr._source_dependencies import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_adr._source_dependencies", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_adr._source_dependencies")
