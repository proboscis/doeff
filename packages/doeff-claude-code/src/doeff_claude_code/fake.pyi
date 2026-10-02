# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = fake.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from dataclasses import replace as replace
from doeff_time import Delay as Delay
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_claude_code.values import ClaudeTurn as ClaudeTurn
from doeff_claude_code.values import FreshSession as FreshSession
from doeff_claude_code.values import ResumeSession as ResumeSession
from doeff_claude_code.values import ForkSession as ForkSession
from doeff_claude_code.values import Rebuilt as Rebuilt
from doeff_claude_code.values import LinkFromHome as LinkFromHome
from doeff_claude_code.values import IMAGE_MIMES as IMAGE_MIMES
from doeff_claude_code.lines import ClaudeStreamLine as ClaudeStreamLine
from doeff_claude_code.lines import Init as Init
from doeff_claude_code.lines import AssistantMessage as AssistantMessage
from doeff_claude_code.lines import ToolResult as ToolResult
from doeff_claude_code.lines import InputFate as InputFate
from doeff_claude_code.lines import PermissionRequested as PermissionRequested
from doeff_claude_code.lines import TaskEvent as TaskEvent
from doeff_claude_code.lines import TurnResult as TurnResult
from doeff_claude_code.lines import Completed as Completed
from doeff_claude_code.lines import Failed as Failed
from doeff_claude_code.lines import Interrupted as Interrupted
from doeff_claude_code.lines import BackendLost as BackendLost
from doeff_claude_code.lines import ClaudeLineKind as ClaudeLineKind
from doeff_claude_code.lines import ClaudeTurnEnd as ClaudeTurnEnd
from doeff_claude_code.lines import Usage as Usage
from doeff_claude_code.effects import ClaudeStartTurn as ClaudeStartTurn
from doeff_claude_code.effects import ClaudeInjectInput as ClaudeInjectInput
from doeff_claude_code.effects import ClaudeInterruptTurn as ClaudeInterruptTurn
from doeff_claude_code.effects import ClaudeReadTurnEvents as ClaudeReadTurnEvents
from doeff_claude_code.effects import ClaudeAnswerPermission as ClaudeAnswerPermission
from doeff_claude_code.effects import ClaudeCloseSession as ClaudeCloseSession
from doeff_claude_code.effects import ClaudeSessionStatus as ClaudeSessionStatus
from doeff_claude_code.effects import ClaudeExportSession as ClaudeExportSession
from doeff_claude_code.effects import TurnStarted as TurnStarted
from doeff_claude_code.effects import InputQueued as InputQueued
from doeff_claude_code.effects import InterruptRequested as InterruptRequested
from doeff_claude_code.effects import TurnEventPage as TurnEventPage
from doeff_claude_code.effects import Answered as Answered
from doeff_claude_code.effects import SessionClosed as SessionClosed
from doeff_claude_code.effects import SessionStatus as SessionStatus
from doeff_claude_code.effects import SessionExported as SessionExported
from doeff_claude_code.effects import Idle as Idle
from doeff_claude_code.effects import TurnRunning as TurnRunning
from doeff_claude_code.effects import Closed as Closed
from doeff_claude_code.effects import TranscriptPresent as TranscriptPresent
from doeff_claude_code.effects import TranscriptAbsent as TranscriptAbsent
from doeff_claude_code.effects import SessionNotFound as SessionNotFound
from doeff_claude_code.effects import SessionIdInUse as SessionIdInUse
from doeff_claude_code.effects import TurnInFlight as TurnInFlight
from doeff_claude_code.effects import AttachmentRefused as AttachmentRefused
from doeff_claude_code.effects import NoTurnInFlight as NoTurnInFlight
from doeff_claude_code.effects import UnknownTurn as UnknownTurn
from doeff_claude_code.effects import NoSuchRequest as NoSuchRequest
from doeff_claude_code.faults import ClaudeDropProcess as ClaudeDropProcess
from doeff_claude_code.faults import ClaudeForgetSession as ClaudeForgetSession
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
QUICK_TURN_SECONDS: float
CLOCK_TICK: float
MIN_SLEEP: float
FAKE_CAPABILITIES: tuple[str, ...]

@dataclass(frozen=True)
class FakeReply:
    text: str
    tool_seconds: float = 0.0
    needs_permission: bool = False
    fail: str | None = None
    lose: str | None = None
    usage: Usage = ...
    cost_usd: float | None = None
    lines: int = 0
    think_seconds: float = 0.0

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class FakeInjection:
    ref: str
    text: str
    fate: str = 'queued'

class FakeTurn:
    seq: int
    started_at: float
    reply: FakeReply
    refs: list[str]
    phase: str
    due_at: float
    lines_emitted: int
    injections: list[FakeInjection]
    permission: str | None
    lines: list[ClaudeStreamLine]
    end: Completed | Failed | Interrupted | BackendLost | None

    def __init__(self, seq: int, started_at: float, reply: FakeReply, refs: tuple[str, ...]) -> None:
        ...

class FakeSession:
    session_id: str
    home: Incomplete
    cwd: str
    current_seq: Incomplete
    next_line_seq: Incomplete
    closed: Incomplete
    turns: dict[int, FakeTurn]

    def __init__(self, session_id: str, home: Incomplete, cwd: str) -> None:
        ...

    def running(self) -> Incomplete:
        ...

class FakeClaudeWorld:
    responder: Incomplete
    respond: Incomplete
    transcripts: Incomplete
    activity: Incomplete
    sessions: Incomplete

    def __init__(self, responder: Incomplete=None, *, respond: Incomplete=None) -> None:
        ...

    def restarted(self) -> Incomplete:
        ...

    def transcript_key(self, home: Incomplete, cwd: str, session_id: str) -> Incomplete:
        ...

def reply_of(world: FakeClaudeWorld, text: str, memory: tuple) -> _Program[FakeReply, object]:
    ...

def emit(session: FakeSession, turn: FakeTurn, kind: ClaudeLineKind) -> _Program[None, object]:
    ...

def emit_all(session: FakeSession, turn: FakeTurn, kinds: list) -> _Program[None, object]:
    ...

def finish(session: FakeSession, turn: FakeTurn, end: ClaudeTurnEnd) -> _Program[None, object]:
    ...

def complete_turn(world: FakeClaudeWorld, session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def line_due_at(turn: FakeTurn, index: int) -> float:
    ...

def next_line_at(turn: FakeTurn) -> float | None:
    ...

def emit_due_lines(session: FakeSession, turn: FakeTurn, now: float) -> _Program[None, object]:
    ...

def end_scripted(session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def advance(world: FakeClaudeWorld, session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def begin_fake_turn(world: FakeClaudeWorld, session: FakeSession, reply: FakeReply, refs: tuple, announce: bool) -> _Program[FakeTurn, object]:
    ...

def transcript_of(world: FakeClaudeWorld, home: Incomplete, cwd: str, session_id: str) -> Incomplete:
    ...

def carry_into(world: FakeClaudeWorld, home: Incomplete, cwd: str, session_id: str, carry: Incomplete) -> Incomplete:
    ...

def fake_start_turn(world: FakeClaudeWorld, request: ClaudeStartTurn) -> _Program[Incomplete, object]:
    ...

def running_turn_of(world: FakeClaudeWorld, turn: ClaudeTurn) -> Incomplete:
    ...

def fake_inject(world: FakeClaudeWorld, request: ClaudeInjectInput) -> _Program[InputQueued | NoTurnInFlight, object]:
    ...

def fake_interrupt(world: FakeClaudeWorld, request: ClaudeInterruptTurn) -> _Program[InterruptRequested | NoTurnInFlight, object]:
    ...

def fake_read_events(world: FakeClaudeWorld, request: ClaudeReadTurnEvents) -> _Program[TurnEventPage | UnknownTurn, object]:
    ...

def fake_answer(world: FakeClaudeWorld, request: ClaudeAnswerPermission) -> _Program[Answered | NoSuchRequest, object]:
    ...

def fake_close(world: FakeClaudeWorld, request: ClaudeCloseSession) -> _Program[SessionClosed, object]:
    ...

def fake_status(world: FakeClaudeWorld, request: ClaudeSessionStatus) -> Incomplete:
    ...

def fake_export(world: FakeClaudeWorld, request: ClaudeExportSession) -> _Program[SessionExported | SessionNotFound, object]:
    ...

def fake_drop(world: FakeClaudeWorld, session_id: str) -> _Program[bool, object]:
    ...

def fake_forget(world: FakeClaudeWorld, session_id: str) -> _Program[bool, object]:
    ...

def fake_claude_code_handler(world: Incomplete) -> _Handler:
    ...
