# moved-submodule-shim: doeff_hy.wire -> doeff_hy._wire
# moved-submodule-shim-names doeff_hy.wire:
#   FrozenMap JsonValue Malformed MalformedField OpaqueJson SHAPE_FORMS UNKNOWN_FIELDS WireShape
#   WireValue codec_of dump dump_json json_schema parse parse_as parse_json parse_json_as
#   wire_config wire_shape
"""Moved: ``doeff_hy.wire`` is now ``doeff_hy._wire`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.wire…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._wire import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._wire", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._wire")
