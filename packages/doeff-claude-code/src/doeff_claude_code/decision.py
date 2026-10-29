# moved-submodule-shim: doeff_claude_code.decision -> doeff_claude_code._decision
# moved-submodule-shim-names doeff_claude_code.decision:
#   ClaudeTurn ForkSession FreshSession Launch LentCredential Refuse ResumeSession Reuse
#   SessionIdInUse SessionNotFound SessionView StartDecision TurnInFlight credential_due retire_time
#   start_decision
"""Moved: ``doeff_claude_code.decision`` is now ``doeff_claude_code._decision`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.decision…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._decision", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._decision")
