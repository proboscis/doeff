# moved-submodule-shim: doeff_hy.pytest_items -> doeff_hy._pytest_items
# moved-submodule-shim-names doeff_hy.pytest_items:
#   Decorator Dynamic FunctionItem ITEMS_ATTR JsonValue LiteralValue MalformedRecord Mark
#   ModuleMarks OpaqueValue ParamValue Parametrize Record SkipIf decode_records encode_records
#   module_record_texts record_at_import record_function record_module_binding
"""Moved: ``doeff_hy.pytest_items`` is now ``doeff_hy._pytest_items`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.pytest_items…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._pytest_items import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._pytest_items", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._pytest_items")
