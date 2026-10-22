# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = lines.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from dataclasses import fields as fields
from dataclasses import replace as replace
from datetime import datetime as datetime
from enum import StrEnum as StrEnum
from doeff import run as run
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_hy.frozen import FrozenJson as FrozenJson
from doeff_hy.frozen import freeze_json as freeze_json
from doeff_hy.frozen import frozen_json_object as frozen_json_object
from doeff_claude_code.values import ClaudeTurn as ClaudeTurn

@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_5m_input_tokens: int | None = None
    cache_creation_1h_input_tokens: int | None = None
    web_search_requests: int | None = None
    service_tier: str | None = None

    def __add__(self, other: Usage) -> Incomplete:
        ...

@dataclass(frozen=True, kw_only=True)
class TimedPhase:
    name: str
    ms: int
    start_ms: int | None = None

@dataclass(frozen=True)
class Init:
    session_id: str
    capabilities: tuple[str, ...] = ...
    model: str = ''
    permission_mode: str = ''
    mcp_servers: tuple[str, ...] = ...
    startup_phases: tuple[TimedPhase, ...] = ...
    startup_origin_ms: int | None = None

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: FrozenMap[FrozenJson] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class AssistantMessage:
    text: str = ''
    tool_calls: tuple[ToolCall, ...] = ...
    usage: Usage | None = None
    model: str | None = None
    parent_tool_use_id: str | None = None
    message_id: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    refusal_category: str | None = None
    request_id: str | None = None

@dataclass(frozen=True)
class ToolAnswer:
    id: str
    text: str
    is_error: bool
    non_text_kinds: tuple[str, ...] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ToolResult:
    answers: tuple[ToolAnswer, ...]

class DeltaKind(StrEnum):
    TEXT = 'text'
    THINKING = 'thinking'
    TOOL_INPUT = 'tool-input'
    OTHER = 'other'
    NO_DELTA = 'no-delta'

@dataclass(frozen=True, kw_only=True)
class CallOpened:
    message_id: str
    model: str | None
    parent_tool_use_id: str | None
    usage: Usage

@dataclass(frozen=True)
class PartialMessage:
    text_delta: str = ''
    delta: DeltaKind = ...
    thinking_delta: str = ''
    tool_input_delta: str = ''
    tool_start: ToolCall | None = None
    ttft_ms: int | None = None
    thinking_start: bool = False
    call_opened: CallOpened | None = None
    call_end_usage: Usage | None = None
    parent_tool_use_id: str | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class CallClosed:
    message_id: str
    model: str | None
    parent_tool_use_id: str | None
    usage: Usage

@dataclass(frozen=True, kw_only=True)
class CallStep:
    open: tuple[CallOpened, ...]
    closed: CallClosed | None

def positive_or(stated: int | None, earlier: int | None) -> _Program[int | None, object]:
    ...

def stated_or(stated: int | str | None, earlier: int | str | None) -> _Program[int | str | None, object]:
    ...

def closed_usage(start: Usage | None, end: Usage | None) -> _Program[Usage | None, object]:
    ...

def call_step(open: tuple, kind: ClaudeLineKind) -> _Program[CallStep, object]:
    ...

@dataclass(frozen=True)
class ThinkingTokens:
    estimated: int = 0
INPUT_FATES: tuple[str, ...]
INPUT_FATE_TERMINAL: tuple[str, ...]

@dataclass(frozen=True)
class InputFate:
    ref: str
    state: str

@dataclass(frozen=True)
class PermissionRequested:
    request_id: str
    tool_name: str
    input: FrozenMap = ...
    suggestions: tuple[object, ...] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ControlResponse:
    request_id: str
    subtype: str
    still_queued: tuple[str, ...] = ...

class HookPhase(StrEnum):
    STARTED = 'started'
    RESPONSE = 'response'

@dataclass(frozen=True, kw_only=True)
class HookNotice:
    event: str
    phase: HookPhase
    name: str
    hook_id: str = ''
    stdout: str = ''
    stderr: str = ''
    output: str = ''
    exit_code: int | None = None
    outcome: str = ''

@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    status: str
    tool_use_id: str | None = None
    task_type: str = ''
    total_tokens: int | None = None
SUBAGENT_TASK_TYPE: str
SYNTHETIC_MODEL: str

class SubagentTranscriptProblem(StrEnum):
    ABSENT = 'absent'
    UNREADABLE = 'unreadable'
    TRUNCATED = 'truncated'

@dataclass(frozen=True, kw_only=True)
class SubagentCall:
    at: datetime
    usage: Usage
    model: str | None = None
    call_id: str | None = None
    request_id: str | None = None

@dataclass(frozen=True, kw_only=True)
class SubagentTranscript:
    agent_id: str
    parent_tool_use_id: str
    calls: tuple[SubagentCall, ...] = ...
    problem: SubagentTranscriptProblem | None = None
    detail: str = ''

@dataclass(frozen=True)
class RateLimit:
    window: str
    utilization: float | None = None
    resets_at: int | None = None
    status: str = ''
RATE_LIMIT_REJECTED: str
ASSISTANT_ERROR_RATE_LIMIT: str

@dataclass(frozen=True)
class AccountLimitHit:
    window: str | None = None
    resets_at: int | None = None
    text: str = ''
ACCOUNT_REFUSAL_ERRORS: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class AccountRefusalHit:
    error: str
    text: str

    def __post_init__(self) -> None:
        ...
STOP_REASON_REFUSAL: str

@dataclass(frozen=True, kw_only=True)
class ModelRefusalHit:
    category: str | None
    model: str | None
    text: str

@dataclass(frozen=True)
class ModelWindow:
    model: str
    context_window: int | None = None
    max_output_tokens: int | None = None

@dataclass(frozen=True)
class ModelUsage:
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    web_search_requests: int | None = None
    cost_usd: float | None = None

@dataclass(frozen=True)
class CostMark:
    total_usd: float
    models: tuple[ModelUsage, ...] | None = None
FRESH_MARK: CostMark
MODEL_COST_TOLERANCE_USD: float

class ModelUsageGap(StrEnum):
    UNKNOWN_MARK = 'unknown-mark'
    NOT_NAMED = 'not-named'
    COUNTED_BACK = 'counted-back'
    UNPRICED = 'unpriced'
    SUM_MISMATCH = 'sum-mismatch'

@dataclass(frozen=True, kw_only=True)
class ModelUsageTally:
    models: tuple[ModelUsage, ...] | None
    gap: ModelUsageGap | None
MODEL_USAGE_COUNTS: tuple[str, ...]

def model_cost_gap(models: tuple | None, cost: float | None) -> _Program[ModelUsageGap | None, object]:
    ...

def usage_at_mark(marked: tuple[ModelUsage, ...], model: str) -> _Program[ModelUsage, object]:
    ...

def model_usage_increment(before: ModelUsage, after: ModelUsage) -> _Program[ModelUsage | None, object]:
    ...

def per_turn_model_usage(mark: CostMark | None, now: tuple[ModelUsage, ...] | None, cost: float | None) -> _Program[ModelUsageTally, object]:
    ...

def merged_windows(earlier: tuple, later: tuple) -> _Program[tuple[ModelWindow, ...], object]:
    ...

@dataclass(frozen=True, kw_only=True)
class RequestTiming:
    time_to_request_ms: int | None = None
    ttft_stream_ms: int | None = None
    first_content_frame_ms: int | None = None
    ttft_ms: int | None = None
    duration_ms: int | None = None
    duration_api_ms: int | None = None
    request_phases: tuple[TimedPhase, ...] = ...

@dataclass(frozen=True)
class TurnResult:
    subtype: str
    is_error: bool
    terminal_reason: str = ''
    origin_kind: str = ''
    result_text: str = ''
    usage: Usage = ...
    cost_usd: float | None = None
    api_error_status: int | None = None
    input_refs: tuple[str, ...] = ...
    model_windows: tuple[ModelWindow, ...] = ...
    model_usage: tuple[ModelUsage, ...] | None = None
    timing: RequestTiming = ...
    structured_output: FrozenJson = None
STOP_HOOK_FEEDBACK_HEAD: str

@dataclass(frozen=True, kw_only=True)
class StopHookFeedback:
    reason: str

class CompactTrigger(StrEnum):
    AUTO = 'auto'
    MANUAL = 'manual'

@dataclass(frozen=True, kw_only=True)
class CompactBoundary:
    trigger: CompactTrigger
    pre_tokens: int
    post_tokens: int | None = None
    cumulative_dropped_tokens: int | None = None
    duration_ms: int | None = None
COMPACTING_STATUS: str

@dataclass(frozen=True, kw_only=True)
class CompactStarted:
    ...

class CompactOutcome(StrEnum):
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    NOT_COMPACTED = 'not_compacted'
COMPACT_ABORTED_ERROR: str

@dataclass(frozen=True, kw_only=True)
class CompactEnded:
    outcome: CompactOutcome
    error: str | None = None

@dataclass(frozen=True)
class Other:
    type: str
    subtype: str = ''
ClaudeLineKind: TypeAlias = Init | AssistantMessage | ToolResult | PartialMessage | ThinkingTokens | InputFate | PermissionRequested | ControlResponse | TaskEvent | HookNotice | RateLimit | StopHookFeedback | CompactStarted | CompactEnded | CompactBoundary | TurnResult | SubagentTranscript | CallClosed | Other

@dataclass(frozen=True)
class ClaudeStreamLine:
    seq: int
    at: datetime
    kind: ClaudeLineKind
    raw: str
STDERR_TAIL_CHARS: int

def stderr_tail_within(text: str) -> _Program[str, object]:
    ...

@dataclass(frozen=True)
class Completed:
    result_text: str = ''
    usage: Usage = ...
    cost_usd: float | None = None
    model_usage: tuple[ModelUsage, ...] | None = None
    model_usage_gap: ModelUsageGap | None = ...
    input_refs: tuple[str, ...] = ...
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...
    account_limit: AccountLimitHit | None = None
    account_refusal: AccountRefusalHit | None = None
    model_refusal: ModelRefusalHit | None = None

@dataclass(frozen=True)
class Failed:
    detail: str
    api_error_status: int | None = None
    terminal_reason: str = ''
    usage: Usage = ...
    cost_usd: float | None = None
    model_usage: tuple[ModelUsage, ...] | None = None
    model_usage_gap: ModelUsageGap | None = ...
    input_refs: tuple[str, ...] = ...
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...
    account_limit: AccountLimitHit | None = None
    account_refusal: AccountRefusalHit | None = None
    model_refusal: ModelRefusalHit | None = None
    exit_code: int | None = None
    stderr_tail: str | None = None

@dataclass(frozen=True)
class Interrupted:
    process_kept: bool
    surviving_refs: tuple[str, ...] = ...
    dropped_refs: tuple[str, ...] = ...
    continued_by: ClaudeTurn | None = None
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...

@dataclass(frozen=True)
class BackendLost:
    detail: str
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...
    exit_code: int | None = None
    stderr_tail: str | None = None
ClaudeTurnEnd: TypeAlias = Completed | Failed | Interrupted | BackendLost

def object_at(value: Incomplete, key: str) -> dict:
    ...

def text_at(value: Incomplete, key: str) -> str:
    ...

def strings_at(value: Incomplete, key: str) -> tuple:
    ...

def int_at(value: Incomplete, key: str) -> int | None:
    ...

def number_at(value: Incomplete, key: str) -> float | None:
    ...

def usage_of(usage: dict) -> Usage:
    ...

def parse_record(line: str) -> dict | None:
    ...

def content_blocks(record: dict) -> tuple:
    ...

def classify_assistant(record: dict) -> Incomplete:
    ...

def tool_answer_of(block: dict) -> _Program[ToolAnswer, object]:
    ...

def classify_user(record: dict) -> Incomplete:
    ...

def hook_notice_of(record: dict, phase: HookPhase) -> _Program[HookNotice, object]:
    ...

def whole_ms_of(number: float | None) -> _Program[int | None, object]:
    ...

def timed_phases_of(phases: dict, starts: dict) -> _Program[tuple[TimedPhase, ...], object]:
    ...

def request_timing_of(record: dict) -> _Program[RequestTiming, object]:
    ...

def compact_boundary_of(record: dict) -> _Program[CompactBoundary | Other, object]:
    ...

def compact_ended_of(record: dict) -> _Program[CompactEnded | Other, object]:
    ...

def classify_system(record: dict) -> Incomplete:
    ...

def classify_rate_limit(record: dict) -> Incomplete:
    ...

def classify_control_request(record: dict) -> Incomplete:
    ...

def classify_control_response(record: dict) -> Incomplete:
    ...

def model_windows_of(model_usage: dict) -> _Program[tuple[ModelWindow, ...], object]:
    ...

def model_usage_of(record: dict) -> _Program[tuple[ModelUsage, ...] | None, object]:
    ...

def classify_result(record: dict) -> Incomplete:
    ...

def classify_lifecycle(record: dict) -> Incomplete:
    ...

def delta_kind_of(delta_type: str) -> _Program[DeltaKind, object]:
    ...

def tool_start_of(event: dict) -> Incomplete:
    ...

def thinking_start_of(event: dict) -> Incomplete:
    ...

def call_opened_of(event: dict, parent: str | None) -> _Program[CallOpened | None, object]:
    ...

def call_end_usage_of(event: dict) -> _Program[Usage | None, object]:
    ...

def classify_stream_event(record: dict) -> Incomplete:
    ...

def recorded_cost(transcript_text: str) -> _Program[CostMark | None, object]:
    ...

def mark_of_result(result: TurnResult) -> _Program[CostMark | None, object]:
    ...

def instant_at(value: dict, key: str) -> _Program[datetime | None, object]:
    ...

def decoded_text(data: bytes) -> _Program[str | None, object]:
    ...

def subagent_calls_of(records: tuple) -> _Program[tuple | None, object]:
    ...

def last_call_total(records: tuple) -> _Program[int | None, object]:
    ...

def subagent_transcript_of(agent_id: str, parent_tool_use_id: str, data: bytes, expected_total: int | None) -> _Program[SubagentTranscript, object]:
    ...

def classify_record(record: dict) -> Incomplete:
    ...
