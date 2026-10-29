# moved-submodule-shim: doeff_claude_code.faults -> doeff_claude_code._faults
"""Moved: ``doeff_claude_code.faults`` is now ``doeff_claude_code._faults`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.faults…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._faults import ClaudeDropProcess as ClaudeDropProcess
    from doeff_claude_code._faults import ClaudeEmitOutsideTurn as ClaudeEmitOutsideTurn
    from doeff_claude_code._faults import ClaudeForgetSession as ClaudeForgetSession
    from doeff_claude_code._faults import ClaudeLiveProcess as ClaudeLiveProcess
    from doeff_claude_code._faults import EffectBase as EffectBase
    from doeff_claude_code._faults import LiveProcess as LiveProcess
    from doeff_claude_code._faults import NoLiveProcess as NoLiveProcess
    from doeff_claude_code._faults import StopReason as StopReason
    from doeff_claude_code._faults import StrEnum as StrEnum
    from doeff_claude_code._faults import dataclass as dataclass

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._faults", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._faults")
