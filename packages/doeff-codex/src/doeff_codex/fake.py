# moved-submodule-shim: doeff_codex.fake -> doeff_codex._fake
# moved-submodule-shim-names doeff_codex.fake:
#   AgentMessageDone BackendLost CodexAnswerRequest CodexCloseSession CodexEvent CodexInput
#   CodexInterruptTurn CodexLaunchCount CodexReadTurnEvents CodexSessionSpec CodexStartTurn
#   CodexSteerTurn CodexTurn FakeCodexWorld FakeReply FakeThread FakeTurn FreshThread
#   InterruptRequested ItemStarted MODULE_TAGS NoSuchRequest NoTurnInFlight ResumeThread
#   SERVER_OVERLOADED_KIND STEERED_PREFIX ServerOverloadedHit SessionClosed Steered TextDelta
#   ThreadUnknown ToolsUnavailable TurnEnded TurnError TurnEventPage TurnInFlight TurnStarted
#   TurnStartedLine TurnStatus USAGE_LIMIT_KIND UnknownTurn UsageLimitHit fake_close
#   fake_codex_handler fake_interrupt fake_launch_count fake_read fake_start fake_steer
"""Moved: ``doeff_codex.fake`` is now ``doeff_codex._fake`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_codex.fake…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_codex._fake import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_codex._fake", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_codex._fake")
