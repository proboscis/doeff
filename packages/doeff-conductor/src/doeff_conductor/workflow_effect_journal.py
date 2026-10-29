# moved-submodule-shim: doeff_conductor.workflow_effect_journal -> doeff_conductor._workflow_effect_journal
# moved-submodule-shim-names doeff_conductor.workflow_effect_journal:
#   EFFECT_KIND_RANDOM EFFECT_KIND_TIME JournalCorruptionError RandomCall TERMINAL_KIND_SUCCEEDED
#   TimeCall WORKFLOW_EFFECT_JOURNAL_FILENAME WORKFLOW_EFFECT_JOURNAL_VERSION WorkflowEffectJournal
#   WorkflowEffectJournalEntry WorkflowEffectReplayDecision WorkflowEffectReplaySession
#   evaluate_random_spec journaled_workflow_effect_handler longest_valid_prefix
#   workflow_effect_cache_key workflow_effect_replay_decision xdg_state_home
"""Moved: ``doeff_conductor.workflow_effect_journal`` is now ``doeff_conductor._workflow_effect_journal`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.workflow_effect_journal…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._workflow_effect_journal import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._workflow_effect_journal", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._workflow_effect_journal")
