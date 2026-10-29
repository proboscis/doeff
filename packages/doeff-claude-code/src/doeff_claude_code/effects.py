# moved-submodule-shim: doeff_claude_code.effects -> doeff_claude_code._effects
# moved-submodule-shim-names doeff_claude_code.effects:
#   AccountLimitHit Allow Answered AttachmentRefused BackendLost CarryRefused ClaudeAnswerPermission
#   ClaudeCloseSession ClaudeExportSession ClaudeHome ClaudeInjectInput ClaudeInterruptTurn
#   ClaudeLiveLimitExceeded ClaudePrintOnce ClaudeReadTurnEvents ClaudeReplaceSessionCredential
#   ClaudeSessionSpec ClaudeSessionStatus ClaudeStartTurn ClaudeStreamLine ClaudeTurn
#   ClaudeWarmSession Closed Completed CredentialReplaced Deny ExportSessionOutcome Failed
#   ForkSession FreshSession HomeCredentialSession Idle InputAlreadyRead InputQueued
#   InterruptRequested Interrupted LaunchFailed LentCredential NoSuchRequest NoSuchSession
#   NoTurnInFlight PrintAnswered PrintContent PrintFailed PrintLimited PrintOutcome PrintTimedOut
#   ProcessStillAlive ReplaceCredentialOutcome ResumeSession SessionClosed SessionExported
#   SessionIdInUse SessionNotFound SessionProcessDown SessionState SessionStatus SessionWarmed
#   StartTurnOutcome TranscriptAbsent TranscriptPresent TranscriptState TurnEventPage TurnInFlight
#   TurnInput TurnRunning TurnStarted UnknownTurn Usage WarmSessionOutcome checked_session_id
#   is_json_object_text
"""Moved: ``doeff_claude_code.effects`` is now ``doeff_claude_code._effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._effects import *  # noqa: F403  # nosemgrep: doeff-no-star-imports

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._effects")
