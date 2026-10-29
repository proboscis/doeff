# moved-submodule-shim: doeff_hy.static_view -> doeff_hy._static_view
# moved-submodule-shim-names doeff_hy.static_view:
#   DEFEFFECT_OUTCOMES_MODULE DEFEFFECT_STATIC_NAMES DEFTEST_FIXTURE_TYPES Finding FixtureType
#   HANDLER_STATIC_NAMES MCP_STATIC_IMPORTS SESSION_STATIC_IMPORTS STATIC_HELPER_IMPORTS
#   TRAVERSE_STATIC_NAMES alias collect_findings deftest_annotation_name deftest_fixture_annotation
#   import_source report_findings static_view static_view_enabled
"""Moved: ``doeff_hy.static_view`` is now ``doeff_hy._static_view`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.static_view…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._static_view import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._static_view", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._static_view")
