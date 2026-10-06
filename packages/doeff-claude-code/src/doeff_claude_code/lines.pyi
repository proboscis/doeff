# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = lines.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from dataclasses import fields as fields
from datetime import datetime as datetime
from enum import StrEnum as StrEnum
from doeff import run as run
from doeff_hy.frozen import FrozenMap as FrozenMap
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

@dataclass(frozen=True)
class Init:
    session_id: str
    capabilities: tuple[str, ...] = ...
    model: str = ''
    permission_mode: str = ''
    mcp_servers: tuple[str, ...] = ...

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: FrozenMap = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class AssistantMessage:
    text: str = ''
    tool_calls: tuple[ToolCall, ...] = ...
    usage: Usage | None = None
    model: str | None = None
    parent_tool_use_id: str | None = None

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

@dataclass(frozen=True)
class PartialMessage:
    text_delta: str = ''
    delta: DeltaKind = ...
    thinking_delta: str = ''

    def __post_init__(self) -> None:
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

@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    status: str

@dataclass(frozen=True)
class RateLimit:
    window: str
    utilization: float | None = None
    resets_at: int | None = None

@dataclass(frozen=True)
class ModelWindow:
    model: str
    context_window: int | None = None
    max_output_tokens: int | None = None

def merged_windows(earlier: tuple, later: tuple) -> _Program[tuple[ModelWindow, ...], object]:
    ...

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

@dataclass(frozen=True)
class Other:
    type: str
    subtype: str = ''
ClaudeLineKind: TypeAlias = Init | AssistantMessage | ToolResult | PartialMessage | ThinkingTokens | InputFate | PermissionRequested | ControlResponse | TaskEvent | HookNotice | RateLimit | TurnResult | Other

@dataclass(frozen=True)
class ClaudeStreamLine:
    seq: int
    at: datetime
    kind: ClaudeLineKind
    raw: str

@dataclass(frozen=True)
class Completed:
    result_text: str = ''
    usage: Usage = ...
    cost_usd: float | None = None
    input_refs: tuple[str, ...] = ...
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...

@dataclass(frozen=True)
class Failed:
    detail: str
    api_error_status: int | None = None
    terminal_reason: str = ''
    usage: Usage = ...
    cost_usd: float | None = None
    input_refs: tuple[str, ...] = ...
    last_call_usage: Usage | None = None
    last_call_model: str | None = None
    model_windows: tuple[ModelWindow, ...] = ...

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

def classify_result(record: dict) -> Incomplete:
    ...

def classify_lifecycle(record: dict) -> Incomplete:
    ...

def delta_kind_of(delta_type: str) -> _Program[DeltaKind, object]:
    ...

def classify_stream_event(record: dict) -> Incomplete:
    ...

def recorded_cost(transcript_text: str) -> _Program[float | None, object]:
    ...

def classify_record(record: dict) -> Incomplete:
    ...
