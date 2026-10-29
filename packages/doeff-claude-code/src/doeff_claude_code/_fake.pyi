# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _fake.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from dataclasses import replace as replace
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_time import WaitWithin as WaitWithin
from doeff_core_effects import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects import ExternalPromise as ExternalPromise
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_hy._frozen import FrozenJson as FrozenJson
from doeff_hy._frozen import frozen_json_object as frozen_json_object
from doeff_claude_code._values import ClaudeTurn as ClaudeTurn
from doeff_claude_code._values import ClaudeSessionSpec as ClaudeSessionSpec
from doeff_claude_code._values import FreshSession as FreshSession
from doeff_claude_code._values import ResumeSession as ResumeSession
from doeff_claude_code._values import ForkSession as ForkSession
from doeff_claude_code._values import Rebuilt as Rebuilt
from doeff_claude_code._values import LinkFromHome as LinkFromHome
from doeff_claude_code._values import IMAGE_MIMES as IMAGE_MIMES
from doeff_claude_code._values import LentCredential as LentCredential
from doeff_claude_code._lines import ClaudeStreamLine as ClaudeStreamLine
from doeff_claude_code._lines import Init as Init
from doeff_claude_code._lines import AssistantMessage as AssistantMessage
from doeff_claude_code._lines import PartialMessage as PartialMessage
from doeff_claude_code._lines import ToolCall as ToolCall
from doeff_claude_code._lines import ToolAnswer as ToolAnswer
from doeff_claude_code._lines import ToolResult as ToolResult
from doeff_claude_code._lines import InputFate as InputFate
from doeff_claude_code._lines import PermissionRequested as PermissionRequested
from doeff_claude_code._lines import tap_lines as tap_lines
from doeff_claude_code._lines import TaskEvent as TaskEvent
from doeff_claude_code._lines import TurnResult as TurnResult
from doeff_claude_code._lines import Completed as Completed
from doeff_claude_code._lines import Failed as Failed
from doeff_claude_code._lines import Interrupted as Interrupted
from doeff_claude_code._lines import BackendLost as BackendLost
from doeff_claude_code._lines import ClaudeLineKind as ClaudeLineKind
from doeff_claude_code._lines import ClaudeTurnEnd as ClaudeTurnEnd
from doeff_claude_code._lines import Usage as Usage
from doeff_claude_code._lines import ModelWindow as ModelWindow
from doeff_claude_code._lines import ModelUsage as ModelUsage
from doeff_claude_code._lines import model_cost_gap as model_cost_gap
from doeff_claude_code._lines import merged_windows as merged_windows
from doeff_claude_code._lines import DeltaKind as DeltaKind
from doeff_claude_code._lines import RateLimit as RateLimit
from doeff_claude_code._lines import AccountLimitHit as AccountLimitHit
from doeff_claude_code._lines import RATE_LIMIT_REJECTED as RATE_LIMIT_REJECTED
from doeff_claude_code._lines import ASSISTANT_ERROR_RATE_LIMIT as ASSISTANT_ERROR_RATE_LIMIT
from doeff_claude_code._lines import StopHookFeedback as StopHookFeedback
from doeff_claude_code._lines import AccountRefusalHit as AccountRefusalHit
from doeff_claude_code._lines import CompactStarted as CompactStarted
from doeff_claude_code._lines import CompactEnded as CompactEnded
from doeff_claude_code._lines import CompactOutcome as CompactOutcome
from doeff_claude_code._lines import CompactBoundary as CompactBoundary
from doeff_claude_code._lines import HookNotice as HookNotice
from doeff_claude_code._lines import HookPhase as HookPhase
from doeff_claude_code._lines import SubagentTranscript as SubagentTranscript
from doeff_claude_code._lines import SUBAGENT_TASK_TYPE as SUBAGENT_TASK_TYPE
from doeff_claude_code._lines import ModelRefusalHit as ModelRefusalHit
from doeff_claude_code._lines import STOP_REASON_REFUSAL as STOP_REASON_REFUSAL
from doeff_claude_code._lines import stderr_tail_within as stderr_tail_within
from doeff_claude_code._lines import CallClosed as CallClosed
from doeff_hy._json_lines import jsonl_lines as jsonl_lines
from doeff import run as run
from doeff_claude_code._dialogue import limit_hit_after as limit_hit_after
from doeff_claude_code._dialogue import refusal_hit_after as refusal_hit_after
from doeff_claude_code._dialogue import model_refusal_after as model_refusal_after
from doeff_claude_code._effects import ClaudeStartTurn as ClaudeStartTurn
from doeff_claude_code._effects import ClaudeInjectInput as ClaudeInjectInput
from doeff_claude_code._effects import ClaudeInterruptTurn as ClaudeInterruptTurn
from doeff_claude_code._effects import ClaudeReadTurnEvents as ClaudeReadTurnEvents
from doeff_claude_code._effects import ClaudeAnswerPermission as ClaudeAnswerPermission
from doeff_claude_code._effects import ClaudeCloseSession as ClaudeCloseSession
from doeff_claude_code._effects import ClaudeSessionStatus as ClaudeSessionStatus
from doeff_claude_code._effects import ClaudeExportSession as ClaudeExportSession
from doeff_claude_code._effects import TurnStarted as TurnStarted
from doeff_claude_code._effects import InputQueued as InputQueued
from doeff_claude_code._effects import InterruptRequested as InterruptRequested
from doeff_claude_code._effects import InputAlreadyRead as InputAlreadyRead
from doeff_claude_code._effects import TurnEventPage as TurnEventPage
from doeff_claude_code._effects import Answered as Answered
from doeff_claude_code._effects import SessionClosed as SessionClosed
from doeff_claude_code._effects import SessionStatus as SessionStatus
from doeff_claude_code._effects import SessionExported as SessionExported
from doeff_claude_code._effects import Idle as Idle
from doeff_claude_code._effects import TurnRunning as TurnRunning
from doeff_claude_code._effects import Closed as Closed
from doeff_claude_code._effects import TranscriptPresent as TranscriptPresent
from doeff_claude_code._effects import TranscriptAbsent as TranscriptAbsent
from doeff_claude_code._effects import SessionNotFound as SessionNotFound
from doeff_claude_code._effects import SessionIdInUse as SessionIdInUse
from doeff_claude_code._effects import TurnInFlight as TurnInFlight
from doeff_claude_code._effects import AttachmentRefused as AttachmentRefused
from doeff_claude_code._effects import NoTurnInFlight as NoTurnInFlight
from doeff_claude_code._effects import UnknownTurn as UnknownTurn
from doeff_claude_code._effects import NoSuchRequest as NoSuchRequest
from doeff_claude_code._effects import ClaudeWarmSession as ClaudeWarmSession
from doeff_claude_code._effects import SessionWarmed as SessionWarmed
from doeff_claude_code._effects import ClaudeLiveLimitExceeded as ClaudeLiveLimitExceeded
from doeff_claude_code._effects import ClaudeReplaceSessionCredential as ClaudeReplaceSessionCredential
from doeff_claude_code._effects import CredentialReplaced as CredentialReplaced
from doeff_claude_code._effects import NoSuchSession as NoSuchSession
from doeff_claude_code._effects import SessionProcessDown as SessionProcessDown
from doeff_claude_code._effects import HomeCredentialSession as HomeCredentialSession
from doeff_claude_code._effects import ClaudePrintOnce as ClaudePrintOnce
from doeff_claude_code._effects import PrintAnswered as PrintAnswered
from doeff_claude_code._effects import PrintFailed as PrintFailed
from doeff_claude_code._effects import PrintLimited as PrintLimited
from doeff_claude_code._effects import PrintTimedOut as PrintTimedOut
from doeff_claude_code._faults import ClaudeDropProcess as ClaudeDropProcess
from doeff_claude_code._faults import ClaudeForgetSession as ClaudeForgetSession
from doeff_claude_code._faults import ClaudeLiveProcess as ClaudeLiveProcess
from doeff_claude_code._faults import ClaudeEmitOutsideTurn as ClaudeEmitOutsideTurn
from doeff_claude_code._faults import LiveProcess as LiveProcess
from doeff_claude_code._faults import NoLiveProcess as NoLiveProcess
from doeff_claude_code._faults import StopReason as StopReason
from doeff_claude_code._argv import launch_key as launch_key
QUICK_TURN_SECONDS: float
CLOCK_TICK: float
MIN_SLEEP: float
DELTA_SECONDS: float
FAKE_THINKING_PIECE: str
FAKE_TOOL_INPUT_PIECE: str
FAKE_CAPABILITIES: tuple[str, ...]
NO_RECEIPT_CAPABILITIES: tuple[str, ...]
FAKE_TOOL_USE_ID: str
FAKE_TOOL_NAME: str
FAKE_REQUEST_PREFIX: str
SYNTHETIC_MODEL: str
SYNTHETIC_USAGE: Usage
REFUSAL_RESULT_SUBTYPE: str
MODEL_REFUSAL_ERROR: str
API_ERROR_SERVER: str

@dataclass(frozen=True, kw_only=True)
class StopHookRejection:
    answer: str
    reason: str

def hyx_scripted_endXquestion_markX(reply: Incomplete) -> bool:
    ...
FAKE_TASK_ID: str

@dataclass(frozen=True)
class FakeReply:
    text: str
    tool_seconds: float = 0.0
    tool_repeats: int = 1
    needs_permission: bool = False
    fail: str | None = None
    lose: str | None = None
    fail_api_status: int | None = None
    lose_exit_code: int | None = None
    lose_stderr: str | None = None
    usage: Usage = ...
    cost_usd: float | None = None
    model_usage: tuple[ModelUsage, ...] | None = None
    lines: int = 0
    think_seconds: float = 0.0
    interrupt_receipt: bool = True
    deltas: int = 0
    tool_input: FrozenMap[FrozenJson] = ...
    tool_output: str = ''
    tool_error: bool = False
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...
    thinking_deltas: int = 0
    tool_input_deltas: int = 0
    account_limit: AccountLimitHit | None = None
    account_refusal: AccountRefusalHit | None = None
    model_refusal: ModelRefusalHit | None = None
    stop_hook_rejections: tuple[StopHookRejection, ...] = ...
    compactions: tuple[CompactBoundary | CompactEnded, ...] = ...
    subagent_transcript: SubagentTranscript | None = None
    hooks: tuple[HookNotice, ...] = ...
    subagent_tool_output: str | None = None
    boundary_injections: bool = False

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
    text: str
    deltas_emitted: int
    injections: list[FakeInjection]
    permission: str | None
    tools_run: int
    lines: list[ClaudeStreamLine]
    end: Completed | Failed | Interrupted | BackendLost | None
    bells: tuple[ExternalPromise[None], ...]
    last_call_usage: Usage | None
    last_call_model: str | None
    model_windows: tuple[ModelWindow, ...]
    limit_hit: AccountLimitHit | None
    refusal_hit: AccountRefusalHit | None
    model_refusal_hit: ModelRefusalHit | None
    subagent_due: SubagentTranscript | None
    boundary_extra: tuple[str, ...]
    call_id: str
    call_usage: Usage | None
    tap: Callable[[ClaudeStreamLine], None] | None

    def __init__(self, seq: int, started_at: float, reply: FakeReply, refs: tuple[str, ...]) -> None:
        ...

class FakeSession:
    session_id: str
    home: Incomplete
    cwd: str
    current_seq: Incomplete
    next_line_seq: Incomplete
    closed: Incomplete
    launches: int
    alive: bool
    launch_key: str | None
    stopped_because: StopReason | None
    warmed_fresh: bool
    credential: LentCredential | None
    turns: dict[int, FakeTurn]

    def __init__(self, session_id: str, home: Incomplete, cwd: str) -> None:
        ...

    def running(self) -> Incomplete:
        ...

class FakeClaudeWorld:
    live_limit: int | None
    responder: Callable[..., object] | None
    respond: Callable[..., object] | None
    transcripts: Incomplete
    activity: Incomplete
    sessions: dict[str, FakeSession]

    def __init__(self, responder: Callable[..., object] | None=None, *, respond: Callable[..., object] | None=None, live_limit: int | None=None) -> None:
        ...

    def restarted(self) -> FakeClaudeWorld:
        ...

    def transcript_key(self, home: Incomplete, cwd: str, session_id: str) -> Incomplete:
        ...

def reply_of(world: FakeClaudeWorld, text: str, memory: tuple) -> _Program[FakeReply, object]:
    ...

def ring_turn(turn: FakeTurn) -> _Program[None, object]:
    ...

def said_by_reply(reply: FakeReply, kind: ClaudeLineKind, call_id: str) -> _Program[ClaudeLineKind, object]:
    ...

def emit(session: FakeSession, turn: FakeTurn, kind: ClaudeLineKind) -> _Program[None, object]:
    ...

def emit_all(session: FakeSession, turn: FakeTurn, kinds: list) -> _Program[None, object]:
    ...

def finish(session: FakeSession, turn: FakeTurn, end: ClaudeTurnEnd) -> _Program[None, object]:
    ...

def read_injections(world: FakeClaudeWorld, session: FakeSession, turn: FakeTurn) -> _Program[tuple, object]:
    ...

def stop_hook_notice_of(rejection: StopHookRejection) -> _Program[HookNotice, object]:
    ...

def begin_text(world: FakeClaudeWorld, session: FakeSession, turn: FakeTurn, now: float) -> _Program[None, object]:
    ...

def next_delta_at(turn: FakeTurn) -> _Program[float | None, object]:
    ...

def emit_due_deltas(session: FakeSession, turn: FakeTurn, now: float) -> _Program[None, object]:
    ...

def emit_limit(session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def complete_turn(world: FakeClaudeWorld, session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def line_due_at(turn: FakeTurn, index: int) -> float:
    ...

def next_line_at(turn: FakeTurn) -> float | None:
    ...

def emit_due_lines(session: FakeSession, turn: FakeTurn, now: float) -> _Program[None, object]:
    ...

def refuse_turn(session: FakeSession, turn: FakeTurn, refusal: AccountRefusalHit) -> _Program[None, object]:
    ...

def model_refuse_turn(session: FakeSession, turn: FakeTurn, refusal: ModelRefusalHit) -> _Program[None, object]:
    ...

def api_error_turn(session: FakeSession, turn: FakeTurn, text: str, status: int) -> _Program[None, object]:
    ...

def end_scripted(session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def repeated_id(base: str, call: int) -> _Program[str, object]:
    ...

def tool_call_lines_of(reply: FakeReply, call: int) -> _Program[list, object]:
    ...

def task_lines_of(reply: FakeReply, call: int, started: bool) -> _Program[list, object]:
    ...

def call_tool(session: FakeSession, turn: FakeTurn, now: float) -> _Program[None, object]:
    ...

def advance(world: FakeClaudeWorld, session: FakeSession, turn: FakeTurn) -> _Program[None, object]:
    ...

def begin_fake_turn(world: FakeClaudeWorld, session: FakeSession, reply: FakeReply, refs: tuple, announce: bool, launched: bool, tap: Callable | None) -> _Program[FakeTurn, object]:
    ...

def note_over_limit(world: FakeClaudeWorld, session: FakeSession, session_id: str, warm: bool) -> _Program[None, object]:
    ...

def transcript_of(world: FakeClaudeWorld, home: Incomplete, cwd: str, session_id: str) -> Incomplete:
    ...

def carry_into(world: FakeClaudeWorld, home: Incomplete, cwd: str, session_id: str, carry: Incomplete) -> Incomplete:
    ...

def fake_start_turn(world: FakeClaudeWorld, request: ClaudeStartTurn) -> _Program[Incomplete, object]:
    ...

def fake_warm_refusal(world: FakeClaudeWorld, origin: FreshSession | ResumeSession, spec: ClaudeSessionSpec) -> _Program[SessionIdInUse | TurnInFlight | SessionNotFound | None, object]:
    ...

def fake_warm_session(world: FakeClaudeWorld, request: ClaudeWarmSession) -> _Program[Incomplete, object]:
    ...

def running_turn_of(world: FakeClaudeWorld, turn: ClaudeTurn) -> Incomplete:
    ...

def fake_inject(world: FakeClaudeWorld, request: ClaudeInjectInput) -> _Program[InputQueued | NoTurnInFlight, object]:
    ...

def fake_interrupt(world: FakeClaudeWorld, request: ClaudeInterruptTurn) -> _Program[InterruptRequested | InputAlreadyRead | NoTurnInFlight, object]:
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

def fake_live_process(world: FakeClaudeWorld, session_id: str) -> _Program[LiveProcess | NoLiveProcess, object]:
    ...

def fake_emit_outside(world: FakeClaudeWorld, session_id: str) -> _Program[bool, object]:
    ...

def fake_replace_credential(world: FakeClaudeWorld, request: ClaudeReplaceSessionCredential) -> _Program[CredentialReplaced | NoSuchSession | SessionProcessDown | HomeCredentialSession, object]:
    ...

def fake_claude_code_handler(world: Incomplete) -> _Handler:
    ...

def fake_print(respond: Callable, request: ClaudePrintOnce) -> _Program[PrintAnswered | PrintFailed | PrintLimited | PrintTimedOut, object]:
    ...

def fake_claude_print_handler(respond: Incomplete) -> _Handler:
    ...
