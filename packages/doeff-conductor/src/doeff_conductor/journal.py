# moved-submodule-shim: doeff_conductor.journal -> doeff_conductor._journal
# moved-submodule-shim-names doeff_conductor.journal:
#   AGENT_JOURNAL_FILENAME AgentEffect AgentError AgentJournal AgentJournalEntry AgentReplayDecision
#   AgentReplaySession AgentTask CreateWorkspaceJournalEntry GATE_ANSWER_JOURNAL_FILENAME
#   GateAnswerJournal GateAnswerJournalEntry JOURNAL_VERSION JournalCorruptionError
#   PROGRESS_JOURNAL_FILENAME PROGRESS_STATUS_FAILED PROGRESS_STATUS_PARKED PROGRESS_STATUS_RUNNING
#   PROGRESS_STATUS_SUCCEEDED ProgressJournal ProgressJournalEntry ResolvedIdentity
#   TERMINAL_KIND_GATE_ANSWER TERMINAL_KIND_OPEN_GATE TERMINAL_KIND_SUCCEEDED
#   TERMINAL_KIND_WORKSPACE_CREATED WORKSPACE_JOURNAL_FILENAME WorkspaceJournal agent_cache_key
#   agent_replay_decision logger longest_valid_prefix node_identity_fingerprint
#   resolve_agent_task_identity resolved_identity_fingerprint xdg_state_home
"""Moved: ``doeff_conductor.journal`` is now ``doeff_conductor._journal`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_conductor.journal…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_conductor._journal import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_conductor._journal", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_conductor._journal")
