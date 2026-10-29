# moved-submodule-shim: doeff_conductor.verbs -> doeff_conductor._verbs
# moved-submodule-shim-names doeff_conductor.verbs:
#   ALLOWED_TERMINAL_KINDS AgentCall BUILT_IN_VALIDATION_SCENARIOS BindingPlan BindingPlanRow
#   DEFAULT_ROUTER_POLICY DEFAULT_SITE_CAPABILITIES ExpandedWorkflow OpenGateView ProfileBinding
#   ProfileRegistry ProgressEvent ResolvedAgentProfile RunStateView SUPPORTED_SUPERVISION_POLICIES
#   ScenarioValidationReport TerminalState ValidationSuiteReport WorkflowSpec agent_cache_key
#   assert_validation_closure describe_author_environment describe_environment
#   load_profile_registry_from_env make_progress_event plan_interpreter plan_workflow
#   resolve_agent_profile save_run_state validate_workflow validation_interpreter
"""Moved: ``doeff_conductor.verbs`` is now ``doeff_conductor._verbs`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.verbs…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._verbs import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._verbs", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._verbs")
