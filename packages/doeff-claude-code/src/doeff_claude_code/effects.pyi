# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff_claude_code.values import ClaudeSessionSpec as ClaudeSessionSpec
from doeff_claude_code.values import ClaudeHome as ClaudeHome
from doeff_claude_code.values import ClaudeTurn as ClaudeTurn
from doeff_claude_code.values import TurnInput as TurnInput
from doeff_claude_code.values import Allow as Allow
from doeff_claude_code.values import Deny as Deny
from doeff_claude_code.values import checked_session_id as checked_session_id
from doeff_claude_code.values import FreshSession as FreshSession
from doeff_claude_code.values import ResumeSession as ResumeSession
from doeff_claude_code.values import ForkSession as ForkSession
from doeff_claude_code.lines import ClaudeStreamLine as ClaudeStreamLine
from doeff_claude_code.lines import Completed as Completed
from doeff_claude_code.lines import Failed as Failed
from doeff_claude_code.lines import Interrupted as Interrupted
from doeff_claude_code.lines import BackendLost as BackendLost

@dataclass(frozen=True)
class ClaudeStartTurn(EffectBase):
    origin: FreshSession | ResumeSession | ForkSession
    spec: ClaudeSessionSpec
    input: TurnInput

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ClaudeInjectInput(EffectBase):
    turn: ClaudeTurn
    input: TurnInput

@dataclass(frozen=True)
class ClaudeInterruptTurn(EffectBase):
    turn: ClaudeTurn

@dataclass(frozen=True)
class ClaudeReadTurnEvents(EffectBase):
    turn: ClaudeTurn
    after_seq: int
    wait_up_to: float

@dataclass(frozen=True)
class ClaudeAnswerPermission(EffectBase):
    turn: ClaudeTurn
    request_id: str
    answer: Allow | Deny

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ClaudeCloseSession(EffectBase):
    session_id: str
    reason: str

@dataclass(frozen=True)
class ClaudeSessionStatus(EffectBase):
    home: ClaudeHome
    cwd: str
    session_id: str

@dataclass(frozen=True)
class ClaudeExportSession(EffectBase):
    home: ClaudeHome
    cwd: str
    session_id: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class TurnStarted:
    turn: ClaudeTurn
    session_id: str

@dataclass(frozen=True)
class InputQueued:
    ref: str

@dataclass(frozen=True)
class InterruptRequested:
    ...

@dataclass(frozen=True)
class TurnEventPage:
    lines: tuple[ClaudeStreamLine, ...]
    next_seq: int
    end: Completed | Failed | Interrupted | BackendLost | None

@dataclass(frozen=True)
class Answered:
    ...

@dataclass(frozen=True)
class SessionClosed:
    was_running: bool

@dataclass(frozen=True)
class Idle:
    ...

@dataclass(frozen=True)
class TurnRunning:
    turn: ClaudeTurn

@dataclass(frozen=True)
class Closed:
    ...
SessionState: TypeAlias = Idle | TurnRunning | Closed

@dataclass(frozen=True)
class TranscriptPresent:
    last_activity: float

@dataclass(frozen=True)
class TranscriptAbsent:
    ...
TranscriptState: TypeAlias = TranscriptPresent | TranscriptAbsent

@dataclass(frozen=True)
class SessionStatus:
    state: Idle | TurnRunning | Closed
    transcript: TranscriptPresent | TranscriptAbsent

@dataclass(frozen=True)
class SessionExported:
    jsonl_text: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class SessionNotFound:
    session_id: str

@dataclass(frozen=True)
class SessionIdInUse:
    session_id: str

@dataclass(frozen=True)
class TurnInFlight:
    turn: ClaudeTurn

@dataclass(frozen=True)
class CarryRefused:
    detail: str

@dataclass(frozen=True)
class LaunchFailed:
    exit_code: int | None = None
    stderr_tail: str = ''

@dataclass(frozen=True)
class AttachmentRefused:
    mime: str

@dataclass(frozen=True)
class NoTurnInFlight:
    session_id: str

@dataclass(frozen=True)
class UnknownTurn:
    turn: ClaudeTurn

@dataclass(frozen=True)
class NoSuchRequest:
    request_id: str

@dataclass(frozen=True)
class ProcessStillAlive:
    detail: str
StartTurnOutcome: TypeAlias = TurnStarted | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed | AttachmentRefused
ExportSessionOutcome: TypeAlias = SessionExported | SessionNotFound

@dataclass(frozen=True, kw_only=True)
class SessionWarmed:
    session_id: str
WarmSessionOutcome: TypeAlias = SessionWarmed | SessionNotFound | SessionIdInUse | TurnInFlight | CarryRefused | LaunchFailed

@_doeff_dataclass(frozen=True)
class ClaudeWarmSession(_doeff_effect_base[WarmSessionOutcome]):
    origin: FreshSession | ResumeSession
    spec: ClaudeSessionSpec

    def __post_init__(self) -> None:
        ...
