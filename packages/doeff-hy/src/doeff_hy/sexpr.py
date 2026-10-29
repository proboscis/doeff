# moved-submodule-shim: doeff_hy.sexpr -> doeff_hy._sexpr
# moved-submodule-shim-names doeff_hy.sexpr:
#   QuotedForms args_of assert_handlers body_of classify_call collect_effects effect_tree
#   list_all_stages list_stages name_of print_all_stages print_effect_tree show_program_analysis
#   stage_of
"""Moved: ``doeff_hy.sexpr`` is now ``doeff_hy._sexpr`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.sexpr…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._sexpr", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._sexpr")
