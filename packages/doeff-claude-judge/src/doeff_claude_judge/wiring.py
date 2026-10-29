# moved-submodule-shim: doeff_claude_judge.wiring -> doeff_claude_judge._wiring
# moved-submodule-shim-names doeff_claude_judge.wiring:
#   BorrowJudgeCredential ClaudeJudgeTarget ClaudeQuery DEFAULT_COMMAND JudgeCredential
#   claude_judge_handler claude_judge_stack fixed_credential_lender production_printer query_user
#   scripted_printer
"""Moved: ``doeff_claude_judge.wiring`` is now ``doeff_claude_judge._wiring`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_judge.wiring…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_judge._wiring import *  # noqa: F403

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_judge._wiring", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_judge._wiring")
