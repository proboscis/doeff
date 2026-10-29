# moved-submodule-shim: doeff_hy.declarations -> doeff_hy._declarations
# moved-submodule-shim-names doeff_hy.declarations:
#   CONTRACT_KEYS DECLARATION_KEYS DefinitionTags EFFECT_KEYS FUNCTION_CONTRACT_KEYS
#   OPTIONAL_TAG_KEYS OUTCOME_KEYS ROLES SPELLS TAG_KEYS answer_value_form bound_answer_methods
#   declaration_setters declared_value defeffect_form effect_field_forms effect_refusal effect_types
#   effects_form field_targets import_form needs_form needs_names outcome_type_forms record_names
#   refuse_unknown_keys runs_carried_names tags_form type_param_symbols
"""Moved: ``doeff_hy.declarations`` is now ``doeff_hy._declarations`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_hy.declarations…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_hy._declarations import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_hy._declarations", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_hy._declarations")
