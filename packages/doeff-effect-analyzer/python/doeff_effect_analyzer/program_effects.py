# moved-submodule-shim: doeff_effect_analyzer.program_effects -> doeff_effect_analyzer._program_effects
# moved-submodule-shim-names doeff_effect_analyzer.program_effects:
#   AllTest AnyTest Basis Binding CarriedProgram ClassTest Clause Comprehension EffectUse Escape
#   ExpansionObserver FunctionNode HandledProgram HandlerEffects Imported Location NotTest PassTest
#   ProgramEffects ReceivedEffect ReceivedField Residual SourcesRead UNBOUND UnknownTest Unresolved
#   analyze_program bindings_for module_ast observe_expansions pass_through qualified_name
#   reading_sources resolve_target runs_where_performed
"""Moved:``doeff_effect_analyzer.program_effects`` is now ``doeff_effect_analyzer._program_effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_effect_analyzer.program_effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_effect_analyzer._program_effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_effect_analyzer._program_effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_effect_analyzer._program_effects")
