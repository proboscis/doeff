# moved-submodule-shim: doeff_claude_code.effects -> doeff_claude_code._effects
"""Moved: ``doeff_claude_code.effects`` is now ``doeff_claude_code._effects`` (ADR-DOE-API-001 R5).

Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on
the old path act on the same objects, and ``python -m doeff_claude_code.effects…`` runs the moved module. Deleted by
``tools/hide_submodules.py finish`` once no repo reads the old path.
"""

import sys
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff_claude_code._effects import AccountLimitHit as AccountLimitHit
    from doeff_claude_code._effects import Allow as Allow
    from doeff_claude_code._effects import Answered as Answered
    from doeff_claude_code._effects import AttachmentRefused as AttachmentRefused
    from doeff_claude_code._effects import BackendLost as BackendLost
    from doeff_claude_code._effects import Callable as Callable
    from doeff_claude_code._effects import CarryRefused as CarryRefused
    from doeff_claude_code._effects import ClaudeAnswerPermission as ClaudeAnswerPermission
    from doeff_claude_code._effects import ClaudeCloseSession as ClaudeCloseSession
    from doeff_claude_code._effects import ClaudeExportSession as ClaudeExportSession
    from doeff_claude_code._effects import ClaudeHome as ClaudeHome
    from doeff_claude_code._effects import ClaudeInjectInput as ClaudeInjectInput
    from doeff_claude_code._effects import ClaudeInterruptTurn as ClaudeInterruptTurn
    from doeff_claude_code._effects import ClaudeLiveLimitExceeded as ClaudeLiveLimitExceeded
    from doeff_claude_code._effects import ClaudePrintOnce as ClaudePrintOnce
    from doeff_claude_code._effects import ClaudeReadTurnEvents as ClaudeReadTurnEvents
    from doeff_claude_code._effects import (
        ClaudeReplaceSessionCredential as ClaudeReplaceSessionCredential,
    )
    from doeff_claude_code._effects import ClaudeSessionSpec as ClaudeSessionSpec
    from doeff_claude_code._effects import ClaudeSessionStatus as ClaudeSessionStatus
    from doeff_claude_code._effects import ClaudeStartTurn as ClaudeStartTurn
    from doeff_claude_code._effects import ClaudeStreamLine as ClaudeStreamLine
    from doeff_claude_code._effects import ClaudeTurn as ClaudeTurn
    from doeff_claude_code._effects import ClaudeWarmSession as ClaudeWarmSession
    from doeff_claude_code._effects import Closed as Closed
    from doeff_claude_code._effects import Completed as Completed
    from doeff_claude_code._effects import CredentialReplaced as CredentialReplaced
    from doeff_claude_code._effects import Deny as Deny
    from doeff_claude_code._effects import EffectBase as EffectBase
    from doeff_claude_code._effects import ExportSessionOutcome as ExportSessionOutcome
    from doeff_claude_code._effects import Failed as Failed
    from doeff_claude_code._effects import ForkSession as ForkSession
    from doeff_claude_code._effects import FreshSession as FreshSession
    from doeff_claude_code._effects import FrozenJson as FrozenJson
    from doeff_claude_code._effects import HomeCredentialSession as HomeCredentialSession
    from doeff_claude_code._effects import Idle as Idle
    from doeff_claude_code._effects import InputAlreadyRead as InputAlreadyRead
    from doeff_claude_code._effects import InputQueued as InputQueued
    from doeff_claude_code._effects import Interrupted as Interrupted
    from doeff_claude_code._effects import InterruptRequested as InterruptRequested
    from doeff_claude_code._effects import LaunchFailed as LaunchFailed
    from doeff_claude_code._effects import LentCredential as LentCredential
    from doeff_claude_code._effects import NoSuchRequest as NoSuchRequest
    from doeff_claude_code._effects import NoSuchSession as NoSuchSession
    from doeff_claude_code._effects import NoTurnInFlight as NoTurnInFlight
    from doeff_claude_code._effects import PrintAnswered as PrintAnswered
    from doeff_claude_code._effects import PrintContent as PrintContent
    from doeff_claude_code._effects import PrintFailed as PrintFailed
    from doeff_claude_code._effects import PrintLimited as PrintLimited
    from doeff_claude_code._effects import PrintOutcome as PrintOutcome
    from doeff_claude_code._effects import PrintTimedOut as PrintTimedOut
    from doeff_claude_code._effects import ProcessStillAlive as ProcessStillAlive
    from doeff_claude_code._effects import ReplaceCredentialOutcome as ReplaceCredentialOutcome
    from doeff_claude_code._effects import ResumeSession as ResumeSession
    from doeff_claude_code._effects import SessionClosed as SessionClosed
    from doeff_claude_code._effects import SessionExported as SessionExported
    from doeff_claude_code._effects import SessionIdInUse as SessionIdInUse
    from doeff_claude_code._effects import SessionNotFound as SessionNotFound
    from doeff_claude_code._effects import SessionProcessDown as SessionProcessDown
    from doeff_claude_code._effects import SessionState as SessionState
    from doeff_claude_code._effects import SessionStatus as SessionStatus
    from doeff_claude_code._effects import SessionWarmed as SessionWarmed
    from doeff_claude_code._effects import StartTurnOutcome as StartTurnOutcome
    from doeff_claude_code._effects import TranscriptAbsent as TranscriptAbsent
    from doeff_claude_code._effects import TranscriptPresent as TranscriptPresent
    from doeff_claude_code._effects import TranscriptState as TranscriptState
    from doeff_claude_code._effects import TurnEventPage as TurnEventPage
    from doeff_claude_code._effects import TurnInFlight as TurnInFlight
    from doeff_claude_code._effects import TurnInput as TurnInput
    from doeff_claude_code._effects import TurnRunning as TurnRunning
    from doeff_claude_code._effects import TurnStarted as TurnStarted
    from doeff_claude_code._effects import UnknownTurn as UnknownTurn
    from doeff_claude_code._effects import Usage as Usage
    from doeff_claude_code._effects import WarmSessionOutcome as WarmSessionOutcome
    from doeff_claude_code._effects import checked_session_id as checked_session_id
    from doeff_claude_code._effects import dataclass as dataclass
    from doeff_claude_code._effects import freeze_json as freeze_json
    from doeff_claude_code._effects import is_json_object_text as is_json_object_text

if __name__ == "__main__":
    import runpy

    runpy.run_module("doeff_claude_code._effects", run_name="__main__", alter_sys=True)
else:
    sys.modules[__name__] = import_module("doeff_claude_code._effects")
