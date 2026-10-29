# moved-submodule-shim: doeff_hy.wire_parity -> doeff_hy._wire_parity
# moved-submodule-shim-names doeff_hy.wire_parity:
#   Codec InputVerdict Malformed MalformedField Mismatch OpaqueJson Report adapter_of codec_of
#   compare_input compare_types inputs_for main mixed_catch_all_unions pydantic_config pydantic_dump
#   pydantic_dump_json pydantic_json_schema pydantic_parse pydantic_parse_json summary thaw_json
#   typed wire_types_in
"""Moved: ``doeff_hy.wire_parity`` is now ``doeff_hy._wire_parity`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.wire_parity…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._wire_parity import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._wire_parity", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._wire_parity")
