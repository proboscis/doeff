"""doeff_claude_code: the effects and handlers of the package (and their types), read on first use."""

from importlib import import_module
from typing import TYPE_CHECKING

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
if TYPE_CHECKING:
    from doeff_claude_code._effects import Answered as Answered
    from doeff_claude_code._effects import AttachmentRefused as AttachmentRefused
    from doeff_claude_code._effects import CarryRefused as CarryRefused
    from doeff_claude_code._effects import ClaudeAnswerPermission as ClaudeAnswerPermission
    from doeff_claude_code._effects import ClaudeCloseSession as ClaudeCloseSession
    from doeff_claude_code._effects import ClaudeExportSession as ClaudeExportSession
    from doeff_claude_code._effects import ClaudeInjectInput as ClaudeInjectInput
    from doeff_claude_code._effects import ClaudeInterruptTurn as ClaudeInterruptTurn
    from doeff_claude_code._effects import ClaudeLiveLimitExceeded as ClaudeLiveLimitExceeded
    from doeff_claude_code._effects import ClaudePrintOnce as ClaudePrintOnce
    from doeff_claude_code._effects import ClaudeReadTurnEvents as ClaudeReadTurnEvents
    from doeff_claude_code._effects import (
        ClaudeReplaceSessionCredential as ClaudeReplaceSessionCredential,
    )
    from doeff_claude_code._effects import ClaudeSessionStatus as ClaudeSessionStatus
    from doeff_claude_code._effects import ClaudeStartTurn as ClaudeStartTurn
    from doeff_claude_code._effects import ClaudeWarmSession as ClaudeWarmSession
    from doeff_claude_code._effects import Closed as Closed
    from doeff_claude_code._effects import CredentialReplaced as CredentialReplaced
    from doeff_claude_code._effects import ExportSessionOutcome as ExportSessionOutcome
    from doeff_claude_code._effects import HomeCredentialSession as HomeCredentialSession
    from doeff_claude_code._effects import Idle as Idle
    from doeff_claude_code._effects import InputAlreadyRead as InputAlreadyRead
    from doeff_claude_code._effects import InputQueued as InputQueued
    from doeff_claude_code._effects import InterruptRequested as InterruptRequested
    from doeff_claude_code._effects import LaunchFailed as LaunchFailed
    from doeff_claude_code._effects import NoSuchRequest as NoSuchRequest
    from doeff_claude_code._effects import NoSuchSession as NoSuchSession
    from doeff_claude_code._effects import NoTurnInFlight as NoTurnInFlight
    from doeff_claude_code._effects import PrintAnswered as PrintAnswered
    from doeff_claude_code._effects import PrintFailed as PrintFailed
    from doeff_claude_code._effects import PrintLimited as PrintLimited
    from doeff_claude_code._effects import PrintOutcome as PrintOutcome
    from doeff_claude_code._effects import PrintTimedOut as PrintTimedOut
    from doeff_claude_code._effects import ProcessStillAlive as ProcessStillAlive
    from doeff_claude_code._effects import ReplaceCredentialOutcome as ReplaceCredentialOutcome
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
    from doeff_claude_code._effects import TurnRunning as TurnRunning
    from doeff_claude_code._effects import TurnStarted as TurnStarted
    from doeff_claude_code._effects import UnknownTurn as UnknownTurn
    from doeff_claude_code._effects import WarmSessionOutcome as WarmSessionOutcome
    from doeff_claude_code._faults import ClaudeDropProcess as ClaudeDropProcess
    from doeff_claude_code._faults import ClaudeEmitOutsideTurn as ClaudeEmitOutsideTurn
    from doeff_claude_code._faults import ClaudeForgetSession as ClaudeForgetSession
    from doeff_claude_code._faults import ClaudeLiveProcess as ClaudeLiveProcess
    from doeff_claude_code._faults import LiveProcess as LiveProcess
    from doeff_claude_code._faults import NoLiveProcess as NoLiveProcess
    from doeff_claude_code._faults import StopReason as StopReason
    from doeff_claude_code._lines import AccountLimitHit as AccountLimitHit
    from doeff_claude_code._lines import AccountRefusalHit as AccountRefusalHit
    from doeff_claude_code._lines import AgentToolResult as AgentToolResult
    from doeff_claude_code._lines import AssistantMessage as AssistantMessage
    from doeff_claude_code._lines import BackendLost as BackendLost
    from doeff_claude_code._lines import CallClosed as CallClosed
    from doeff_claude_code._lines import CallOpened as CallOpened
    from doeff_claude_code._lines import ClaudeLineKind as ClaudeLineKind
    from doeff_claude_code._lines import ClaudeStreamLine as ClaudeStreamLine
    from doeff_claude_code._lines import ClaudeTurnEnd as ClaudeTurnEnd
    from doeff_claude_code._lines import CompactBoundary as CompactBoundary
    from doeff_claude_code._lines import CompactEnded as CompactEnded
    from doeff_claude_code._lines import CompactOutcome as CompactOutcome
    from doeff_claude_code._lines import CompactStarted as CompactStarted
    from doeff_claude_code._lines import CompactTrigger as CompactTrigger
    from doeff_claude_code._lines import Completed as Completed
    from doeff_claude_code._lines import ControlResponse as ControlResponse
    from doeff_claude_code._lines import DeltaKind as DeltaKind
    from doeff_claude_code._lines import Failed as Failed
    from doeff_claude_code._lines import HookNotice as HookNotice
    from doeff_claude_code._lines import HookPhase as HookPhase
    from doeff_claude_code._lines import Init as Init
    from doeff_claude_code._lines import InputFate as InputFate
    from doeff_claude_code._lines import Interrupted as Interrupted
    from doeff_claude_code._lines import ModelRefusalHit as ModelRefusalHit
    from doeff_claude_code._lines import ModelUsage as ModelUsage
    from doeff_claude_code._lines import ModelUsageGap as ModelUsageGap
    from doeff_claude_code._lines import ModelWindow as ModelWindow
    from doeff_claude_code._lines import Other as Other
    from doeff_claude_code._lines import PartialMessage as PartialMessage
    from doeff_claude_code._lines import PermissionRequested as PermissionRequested
    from doeff_claude_code._lines import RateLimit as RateLimit
    from doeff_claude_code._lines import RequestTiming as RequestTiming
    from doeff_claude_code._lines import StopHookFeedback as StopHookFeedback
    from doeff_claude_code._lines import SubagentCall as SubagentCall
    from doeff_claude_code._lines import SubagentTranscript as SubagentTranscript
    from doeff_claude_code._lines import SubagentTranscriptProblem as SubagentTranscriptProblem
    from doeff_claude_code._lines import TaskEvent as TaskEvent
    from doeff_claude_code._lines import ThinkingTokens as ThinkingTokens
    from doeff_claude_code._lines import TimedPhase as TimedPhase
    from doeff_claude_code._lines import ToolAnswer as ToolAnswer
    from doeff_claude_code._lines import ToolCall as ToolCall
    from doeff_claude_code._lines import ToolResult as ToolResult
    from doeff_claude_code._lines import TurnResult as TurnResult
    from doeff_claude_code._lines import Usage as Usage
    from doeff_claude_code._values import Allow as Allow
    from doeff_claude_code._values import AskHost as AskHost
    from doeff_claude_code._values import AutocompactAuto as AutocompactAuto
    from doeff_claude_code._values import AutocompactTokens as AutocompactTokens
    from doeff_claude_code._values import AutocompactWindow as AutocompactWindow
    from doeff_claude_code._values import BypassAll as BypassAll
    from doeff_claude_code._values import ClaudeHome as ClaudeHome
    from doeff_claude_code._values import ClaudeSessionSpec as ClaudeSessionSpec
    from doeff_claude_code._values import ClaudeTurn as ClaudeTurn
    from doeff_claude_code._values import Deny as Deny
    from doeff_claude_code._values import DenyUnlisted as DenyUnlisted
    from doeff_claude_code._values import ForkSession as ForkSession
    from doeff_claude_code._values import FreshSession as FreshSession
    from doeff_claude_code._values import HomeSettings as HomeSettings
    from doeff_claude_code._values import ImageAttachment as ImageAttachment
    from doeff_claude_code._values import LentCredential as LentCredential
    from doeff_claude_code._values import LinkFromHome as LinkFromHome
    from doeff_claude_code._values import McpServer as McpServer
    from doeff_claude_code._values import McpSse as McpSse
    from doeff_claude_code._values import McpStdio as McpStdio
    from doeff_claude_code._values import PermissionAnswer as PermissionAnswer
    from doeff_claude_code._values import PermissionPolicy as PermissionPolicy
    from doeff_claude_code._values import PrintContent as PrintContent
    from doeff_claude_code._values import Rebuilt as Rebuilt
    from doeff_claude_code._values import ResumeSession as ResumeSession
    from doeff_claude_code._values import SessionOrigin as SessionOrigin
    from doeff_claude_code._values import TranscriptCarry as TranscriptCarry
    from doeff_claude_code._values import TurnInput as TurnInput
    from doeff_claude_code._fake import FakeClaudeWorld as FakeClaudeWorld
    from doeff_claude_code._fake import FakeReply as FakeReply
    from doeff_claude_code._fake import StopHookRejection as StopHookRejection
    from doeff_claude_code._fake import fake_claude_code_handler as fake_claude_code_handler
    from doeff_claude_code._fake import fake_claude_print_handler as fake_claude_print_handler
    from doeff_claude_code._print_once import claude_print_handler as claude_print_handler

_LAZY_EXPORTS: dict[str, str] = {
    "AccountLimitHit": "doeff_claude_code._lines",
    "AccountRefusalHit": "doeff_claude_code._lines",
    "AgentToolResult": "doeff_claude_code._lines",
    "AssistantMessage": "doeff_claude_code._lines",
    "BackendLost": "doeff_claude_code._lines",
    "CallClosed": "doeff_claude_code._lines",
    "CallOpened": "doeff_claude_code._lines",
    "ClaudeLineKind": "doeff_claude_code._lines",
    "ClaudeStreamLine": "doeff_claude_code._lines",
    "ClaudeTurnEnd": "doeff_claude_code._lines",
    "CompactBoundary": "doeff_claude_code._lines",
    "CompactEnded": "doeff_claude_code._lines",
    "CompactOutcome": "doeff_claude_code._lines",
    "CompactStarted": "doeff_claude_code._lines",
    "CompactTrigger": "doeff_claude_code._lines",
    "Completed": "doeff_claude_code._lines",
    "ControlResponse": "doeff_claude_code._lines",
    "DeltaKind": "doeff_claude_code._lines",
    "Failed": "doeff_claude_code._lines",
    "HookNotice": "doeff_claude_code._lines",
    "HookPhase": "doeff_claude_code._lines",
    "Init": "doeff_claude_code._lines",
    "InputFate": "doeff_claude_code._lines",
    "Interrupted": "doeff_claude_code._lines",
    "ModelRefusalHit": "doeff_claude_code._lines",
    "ModelUsage": "doeff_claude_code._lines",
    "ModelUsageGap": "doeff_claude_code._lines",
    "ModelWindow": "doeff_claude_code._lines",
    "Other": "doeff_claude_code._lines",
    "PartialMessage": "doeff_claude_code._lines",
    "PermissionRequested": "doeff_claude_code._lines",
    "RateLimit": "doeff_claude_code._lines",
    "RequestTiming": "doeff_claude_code._lines",
    "StopHookFeedback": "doeff_claude_code._lines",
    "SubagentCall": "doeff_claude_code._lines",
    "SubagentTranscript": "doeff_claude_code._lines",
    "SubagentTranscriptProblem": "doeff_claude_code._lines",
    "TaskEvent": "doeff_claude_code._lines",
    "ThinkingTokens": "doeff_claude_code._lines",
    "TimedPhase": "doeff_claude_code._lines",
    "ToolAnswer": "doeff_claude_code._lines",
    "ToolCall": "doeff_claude_code._lines",
    "ToolResult": "doeff_claude_code._lines",
    "TurnResult": "doeff_claude_code._lines",
    "Usage": "doeff_claude_code._lines",
    "Answered": "doeff_claude_code._effects",
    "AttachmentRefused": "doeff_claude_code._effects",
    "CarryRefused": "doeff_claude_code._effects",
    "ClaudeAnswerPermission": "doeff_claude_code._effects",
    "ClaudeCloseSession": "doeff_claude_code._effects",
    "ClaudeExportSession": "doeff_claude_code._effects",
    "ClaudeInjectInput": "doeff_claude_code._effects",
    "ClaudeInterruptTurn": "doeff_claude_code._effects",
    "ClaudeLiveLimitExceeded": "doeff_claude_code._effects",
    "ClaudePrintOnce": "doeff_claude_code._effects",
    "ClaudeReadTurnEvents": "doeff_claude_code._effects",
    "ClaudeReplaceSessionCredential": "doeff_claude_code._effects",
    "ClaudeSessionStatus": "doeff_claude_code._effects",
    "ClaudeStartTurn": "doeff_claude_code._effects",
    "ClaudeWarmSession": "doeff_claude_code._effects",
    "Closed": "doeff_claude_code._effects",
    "CredentialReplaced": "doeff_claude_code._effects",
    "ExportSessionOutcome": "doeff_claude_code._effects",
    "HomeCredentialSession": "doeff_claude_code._effects",
    "Idle": "doeff_claude_code._effects",
    "InputAlreadyRead": "doeff_claude_code._effects",
    "InputQueued": "doeff_claude_code._effects",
    "InterruptRequested": "doeff_claude_code._effects",
    "LaunchFailed": "doeff_claude_code._effects",
    "NoSuchRequest": "doeff_claude_code._effects",
    "NoSuchSession": "doeff_claude_code._effects",
    "NoTurnInFlight": "doeff_claude_code._effects",
    "PrintAnswered": "doeff_claude_code._effects",
    "PrintFailed": "doeff_claude_code._effects",
    "PrintLimited": "doeff_claude_code._effects",
    "PrintOutcome": "doeff_claude_code._effects",
    "PrintTimedOut": "doeff_claude_code._effects",
    "ProcessStillAlive": "doeff_claude_code._effects",
    "ReplaceCredentialOutcome": "doeff_claude_code._effects",
    "SessionClosed": "doeff_claude_code._effects",
    "SessionExported": "doeff_claude_code._effects",
    "SessionIdInUse": "doeff_claude_code._effects",
    "SessionNotFound": "doeff_claude_code._effects",
    "SessionProcessDown": "doeff_claude_code._effects",
    "SessionState": "doeff_claude_code._effects",
    "SessionStatus": "doeff_claude_code._effects",
    "SessionWarmed": "doeff_claude_code._effects",
    "StartTurnOutcome": "doeff_claude_code._effects",
    "TranscriptAbsent": "doeff_claude_code._effects",
    "TranscriptPresent": "doeff_claude_code._effects",
    "TranscriptState": "doeff_claude_code._effects",
    "TurnEventPage": "doeff_claude_code._effects",
    "TurnInFlight": "doeff_claude_code._effects",
    "TurnRunning": "doeff_claude_code._effects",
    "TurnStarted": "doeff_claude_code._effects",
    "UnknownTurn": "doeff_claude_code._effects",
    "WarmSessionOutcome": "doeff_claude_code._effects",
    "Allow": "doeff_claude_code._values",
    "AskHost": "doeff_claude_code._values",
    "AutocompactAuto": "doeff_claude_code._values",
    "AutocompactTokens": "doeff_claude_code._values",
    "AutocompactWindow": "doeff_claude_code._values",
    "BypassAll": "doeff_claude_code._values",
    "ClaudeHome": "doeff_claude_code._values",
    "ClaudeSessionSpec": "doeff_claude_code._values",
    "ClaudeTurn": "doeff_claude_code._values",
    "Deny": "doeff_claude_code._values",
    "DenyUnlisted": "doeff_claude_code._values",
    "ForkSession": "doeff_claude_code._values",
    "FreshSession": "doeff_claude_code._values",
    "HomeSettings": "doeff_claude_code._values",
    "ImageAttachment": "doeff_claude_code._values",
    "LentCredential": "doeff_claude_code._values",
    "LinkFromHome": "doeff_claude_code._values",
    "McpServer": "doeff_claude_code._values",
    "McpSse": "doeff_claude_code._values",
    "McpStdio": "doeff_claude_code._values",
    "PermissionAnswer": "doeff_claude_code._values",
    "PermissionPolicy": "doeff_claude_code._values",
    "PrintContent": "doeff_claude_code._values",
    "Rebuilt": "doeff_claude_code._values",
    "ResumeSession": "doeff_claude_code._values",
    "SessionOrigin": "doeff_claude_code._values",
    "TranscriptCarry": "doeff_claude_code._values",
    "TurnInput": "doeff_claude_code._values",
    "ClaudeDropProcess": "doeff_claude_code._faults",
    "ClaudeEmitOutsideTurn": "doeff_claude_code._faults",
    "ClaudeForgetSession": "doeff_claude_code._faults",
    "ClaudeLiveProcess": "doeff_claude_code._faults",
    "LiveProcess": "doeff_claude_code._faults",
    "NoLiveProcess": "doeff_claude_code._faults",
    "StopReason": "doeff_claude_code._faults",
    "ClaudeCodeHost": "doeff_claude_code._handler",
    "claude_code_handler": "doeff_claude_code._handler",
    "FakeClaudeWorld": "doeff_claude_code._fake",
    "FakeReply": "doeff_claude_code._fake",
    "StopHookRejection": "doeff_claude_code._fake",
    "fake_claude_code_handler": "doeff_claude_code._fake",
    "fake_claude_print_handler": "doeff_claude_code._fake",
    "claude_print_handler": "doeff_claude_code._print_once",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import hy  # noqa: F401 - the modules behind `_` are Hy modules; importing hy lets Python find them

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
