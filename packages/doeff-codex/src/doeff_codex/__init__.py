"""doeff_codex: the effects and handlers of the package (and their types), read on first use."""

from importlib import import_module
from typing import TYPE_CHECKING

# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers; `__all__` is not used (DOEFF021).
if TYPE_CHECKING:
    from doeff_codex._effects import Answered as Answered
    from doeff_codex._effects import BackendLost as BackendLost
    from doeff_codex._effects import CodexAnswerRequest as CodexAnswerRequest
    from doeff_codex._effects import CodexCloseSession as CodexCloseSession
    from doeff_codex._effects import CodexInterruptTurn as CodexInterruptTurn
    from doeff_codex._effects import CodexLaunchCount as CodexLaunchCount
    from doeff_codex._effects import CodexReadTurnEvents as CodexReadTurnEvents
    from doeff_codex._effects import CodexStartTurn as CodexStartTurn
    from doeff_codex._effects import CodexSteerTurn as CodexSteerTurn
    from doeff_codex._effects import CodexTurnEnd as CodexTurnEnd
    from doeff_codex._effects import InterruptRequested as InterruptRequested
    from doeff_codex._effects import LaunchFailed as LaunchFailed
    from doeff_codex._effects import NoSuchRequest as NoSuchRequest
    from doeff_codex._effects import NoTurnInFlight as NoTurnInFlight
    from doeff_codex._effects import ProcessStillAlive as ProcessStillAlive
    from doeff_codex._effects import RequestRefused as RequestRefused
    from doeff_codex._effects import SessionClosed as SessionClosed
    from doeff_codex._effects import StartTurnOutcome as StartTurnOutcome
    from doeff_codex._effects import Steered as Steered
    from doeff_codex._effects import ThreadUnknown as ThreadUnknown
    from doeff_codex._effects import ToolsUnavailable as ToolsUnavailable
    from doeff_codex._effects import TurnEventPage as TurnEventPage
    from doeff_codex._effects import TurnInFlight as TurnInFlight
    from doeff_codex._effects import TurnStarted as TurnStarted
    from doeff_codex._effects import UnknownTurn as UnknownTurn
    from doeff_codex._fake import FakeCodexWorld as FakeCodexWorld
    from doeff_codex._fake import FakeReply as FakeReply
    from doeff_codex._fake import fake_codex_handler as fake_codex_handler
    from doeff_codex._handler import CodexHost as CodexHost
    from doeff_codex._handler import codex_handler as codex_handler
    from doeff_codex._lines import AgentCall as AgentCall
    from doeff_codex._lines import AgentMessageDone as AgentMessageDone
    from doeff_codex._lines import CodexLine as CodexLine
    from doeff_codex._lines import CommandRun as CommandRun
    from doeff_codex._lines import DynamicCall as DynamicCall
    from doeff_codex._lines import FilePatch as FilePatch
    from doeff_codex._lines import ImageView as ImageView
    from doeff_codex._lines import ItemDone as ItemDone
    from doeff_codex._lines import ItemStarted as ItemStarted
    from doeff_codex._lines import McpCall as McpCall
    from doeff_codex._lines import ReasoningDelta as ReasoningDelta
    from doeff_codex._lines import ServerOverloadedHit as ServerOverloadedHit
    from doeff_codex._lines import ServerRequest as ServerRequest
    from doeff_codex._lines import TextDelta as TextDelta
    from doeff_codex._lines import TokenCount as TokenCount
    from doeff_codex._lines import TokenUsage as TokenUsage
    from doeff_codex._lines import ToolItem as ToolItem
    from doeff_codex._lines import ToolStatus as ToolStatus
    from doeff_codex._lines import TurnEnded as TurnEnded
    from doeff_codex._lines import TurnStatus as TurnStatus
    from doeff_codex._lines import UsageLimitHit as UsageLimitHit
    from doeff_codex._lines import WebSearch as WebSearch
    from doeff_codex._rpc import ApprovalPolicy as ApprovalPolicy
    from doeff_codex._rpc import SandboxMode as SandboxMode
    from doeff_codex._values import CodexEvent as CodexEvent
    from doeff_codex._values import CodexHome as CodexHome
    from doeff_codex._values import CodexImage as CodexImage
    from doeff_codex._values import CodexInput as CodexInput
    from doeff_codex._values import CodexSessionSpec as CodexSessionSpec
    from doeff_codex._values import CodexTurn as CodexTurn
    from doeff_codex._values import FreshThread as FreshThread
    from doeff_codex._values import ResumeThread as ResumeThread

_LAZY_EXPORTS: dict[str, str] = {
    "Answered": "doeff_codex._effects",
    "BackendLost": "doeff_codex._effects",
    "CodexAnswerRequest": "doeff_codex._effects",
    "CodexCloseSession": "doeff_codex._effects",
    "CodexInterruptTurn": "doeff_codex._effects",
    "CodexLaunchCount": "doeff_codex._effects",
    "CodexReadTurnEvents": "doeff_codex._effects",
    "CodexStartTurn": "doeff_codex._effects",
    "CodexSteerTurn": "doeff_codex._effects",
    "CodexTurnEnd": "doeff_codex._effects",
    "InterruptRequested": "doeff_codex._effects",
    "LaunchFailed": "doeff_codex._effects",
    "NoSuchRequest": "doeff_codex._effects",
    "NoTurnInFlight": "doeff_codex._effects",
    "ProcessStillAlive": "doeff_codex._effects",
    "RequestRefused": "doeff_codex._effects",
    "SessionClosed": "doeff_codex._effects",
    "StartTurnOutcome": "doeff_codex._effects",
    "Steered": "doeff_codex._effects",
    "ThreadUnknown": "doeff_codex._effects",
    "ToolsUnavailable": "doeff_codex._effects",
    "TurnEventPage": "doeff_codex._effects",
    "TurnInFlight": "doeff_codex._effects",
    # The answer of CodexStartTurn. `_lines` has a line record of the same name (a CodexLine member), read from
    # `doeff_codex._lines` where the line records are classified.
    "TurnStarted": "doeff_codex._effects",
    "UnknownTurn": "doeff_codex._effects",
    "FakeCodexWorld": "doeff_codex._fake",
    "FakeReply": "doeff_codex._fake",
    "fake_codex_handler": "doeff_codex._fake",
    "CodexHost": "doeff_codex._handler",
    "codex_handler": "doeff_codex._handler",
    "AgentCall": "doeff_codex._lines",
    "AgentMessageDone": "doeff_codex._lines",
    "CodexLine": "doeff_codex._lines",
    "CommandRun": "doeff_codex._lines",
    "DynamicCall": "doeff_codex._lines",
    "FilePatch": "doeff_codex._lines",
    "ImageView": "doeff_codex._lines",
    "ItemDone": "doeff_codex._lines",
    "ItemStarted": "doeff_codex._lines",
    "McpCall": "doeff_codex._lines",
    "ReasoningDelta": "doeff_codex._lines",
    "ServerOverloadedHit": "doeff_codex._lines",
    "ServerRequest": "doeff_codex._lines",
    "TextDelta": "doeff_codex._lines",
    "TokenCount": "doeff_codex._lines",
    "TokenUsage": "doeff_codex._lines",
    "ToolItem": "doeff_codex._lines",
    "ToolStatus": "doeff_codex._lines",
    "TurnEnded": "doeff_codex._lines",
    "TurnStatus": "doeff_codex._lines",
    "UsageLimitHit": "doeff_codex._lines",
    "WebSearch": "doeff_codex._lines",
    "ApprovalPolicy": "doeff_codex._rpc",
    "SandboxMode": "doeff_codex._rpc",
    "CodexEvent": "doeff_codex._values",
    "CodexHome": "doeff_codex._values",
    "CodexImage": "doeff_codex._values",
    "CodexInput": "doeff_codex._values",
    "CodexSessionSpec": "doeff_codex._values",
    "CodexTurn": "doeff_codex._values",
    "FreshThread": "doeff_codex._values",
    "ResumeThread": "doeff_codex._values",
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
