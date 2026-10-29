# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _effects.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from doeff_hy.frozen import FrozenJson as FrozenJson
from doeff_hy.frozen import freeze_json as freeze_json
from doeff_claude_code._values import ClaudeSessionSpec as ClaudeSessionSpec
from doeff_claude_code._values import ClaudeHome as ClaudeHome
from doeff_claude_code._values import ClaudeTurn as ClaudeTurn
from doeff_claude_code._values import TurnInput as TurnInput
from doeff_claude_code._values import Allow as Allow
from doeff_claude_code._values import Deny as Deny
from doeff_claude_code._values import checked_session_id as checked_session_id
from doeff_claude_code._values import FreshSession as FreshSession
from doeff_claude_code._values import ResumeSession as ResumeSession
from doeff_claude_code._values import ForkSession as ForkSession
from doeff_claude_code._values import LentCredential as LentCredential
from doeff_claude_code._values import PrintContent as PrintContent
from doeff_claude_code._lines import ClaudeStreamLine as ClaudeStreamLine
from doeff_claude_code._lines import Completed as Completed
from doeff_claude_code._lines import Failed as Failed
from doeff_claude_code._lines import Interrupted as Interrupted
from doeff_claude_code._lines import BackendLost as BackendLost
from doeff_claude_code._lines import Usage as Usage
from doeff_claude_code._lines import AccountLimitHit as AccountLimitHit

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
class InputAlreadyRead:
    ref: str

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

@dataclass(frozen=True)
class ClaudeStartTurn(EffectBase[StartTurnOutcome]):
    origin: FreshSession | ResumeSession | ForkSession
    spec: ClaudeSessionSpec
    input: TurnInput
    tap: Callable[[ClaudeStreamLine], None] | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ClaudeInjectInput(EffectBase[InputQueued | NoTurnInFlight | AttachmentRefused]):
    turn: ClaudeTurn
    input: TurnInput

@dataclass(frozen=True)
class ClaudeInterruptTurn(EffectBase[InterruptRequested | InputAlreadyRead | NoTurnInFlight]):
    turn: ClaudeTurn
    for_input: str | None = None

@dataclass(frozen=True)
class ClaudeReadTurnEvents(EffectBase[TurnEventPage | UnknownTurn]):
    turn: ClaudeTurn
    after_seq: int
    wait_up_to: float

@dataclass(frozen=True)
class ClaudeAnswerPermission(EffectBase[Answered | NoSuchRequest]):
    turn: ClaudeTurn
    request_id: str
    answer: Allow | Deny

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ClaudeCloseSession(EffectBase[SessionClosed | ProcessStillAlive]):
    session_id: str
    reason: str

@dataclass(frozen=True)
class ClaudeSessionStatus(EffectBase[SessionStatus]):
    home: ClaudeHome
    cwd: str
    session_id: str

@dataclass(frozen=True)
class ClaudeExportSession(EffectBase[ExportSessionOutcome]):
    home: ClaudeHome
    cwd: str
    session_id: str
    tail_bytes: int | None = None
    from_byte: int | None = None

    def __post_init__(self) -> None:
        ...

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

@_doeff_dataclass(frozen=True)
class ClaudeLiveLimitExceeded(_doeff_effect_base[None]):
    session_id: str
    live: int
    limit: int
    warm: bool

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class CredentialReplaced:
    session_id: str

@dataclass(frozen=True, kw_only=True)
class NoSuchSession:
    session_id: str

@dataclass(frozen=True, kw_only=True)
class SessionProcessDown:
    session_id: str

@dataclass(frozen=True, kw_only=True)
class HomeCredentialSession:
    session_id: str
ReplaceCredentialOutcome: TypeAlias = CredentialReplaced | NoSuchSession | SessionProcessDown | HomeCredentialSession

@_doeff_dataclass(frozen=True)
class ClaudeReplaceSessionCredential(_doeff_effect_base[ReplaceCredentialOutcome]):
    session_id: str
    credential: LentCredential

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class PrintAnswered:
    structured_output: FrozenJson
    usage: Usage
    cost_usd: float | None
    models: tuple[str, ...]
    elapsed_ms: int
    api_ms: int | None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class PrintFailed:
    exit_code: int | None
    api_status: int | None
    detail: str
    stderr_tail: str

@dataclass(frozen=True, kw_only=True)
class PrintTimedOut:
    seconds: float
    stderr_tail: str

@dataclass(frozen=True, kw_only=True)
class PrintLimited:
    limit: AccountLimitHit
    exit_code: int | None
    stderr_tail: str
PrintOutcome: TypeAlias = PrintAnswered | PrintFailed | PrintLimited | PrintTimedOut

def is_json_object_text(text: object) -> bool:
    ...

@_doeff_dataclass(frozen=True)
class ClaudePrintOnce(_doeff_effect_base[PrintOutcome]):
    home: ClaudeHome
    model: str
    system_prompt: str
    json_schema: str
    input: str | PrintContent
    timeout_seconds: float
    credential: LentCredential | None = None
    thinking_tokens: int | None = None
    effort: str | None = None

    def __post_init__(self) -> None:
        ...
