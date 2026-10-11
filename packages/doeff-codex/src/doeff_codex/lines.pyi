# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = lines.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import Program as _Program
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_hy.json_value import OpaqueJson as OpaqueJson
from doeff_hy.wire import parse as parse
from doeff_hy.wire import parse_json as parse_json
from doeff_hy.wire import Malformed as Malformed

class TurnStatus(StrEnum):
    COMPLETED = 'completed'
    INTERRUPTED = 'interrupted'
    FAILED = 'failed'
    IN_PROGRESS = 'inProgress'
USAGE_LIMIT_KIND: str

class ToolStatus(StrEnum):
    COMPLETED = 'completed'
    FAILED = 'failed'
    DECLINED = 'declined'
    IN_PROGRESS = 'inProgress'

@dataclass(frozen=True, kw_only=True)
class RpcErrorWire:
    code: int
    message: str

@dataclass(frozen=True, kw_only=True)
class EnvelopeWire:
    id: int | str | None = None
    method: str | None = None
    params: OpaqueJson | None = None
    result: OpaqueJson | None = None
    error: RpcErrorWire | None = None

@dataclass(frozen=True, kw_only=True)
class IdWire:
    id: str

@dataclass(frozen=True, kw_only=True)
class ResultWire:
    thread: IdWire | None = None
    turn: IdWire | None = None

@dataclass(frozen=True, kw_only=True)
class ThreadParamsWire:
    thread: IdWire

@dataclass(frozen=True, kw_only=True)
class HttpFailureWire:
    http_status_code: int | None = None

@dataclass(frozen=True, kw_only=True)
class ErrorInfoWire:
    http_connection_failed: HttpFailureWire | None = None
    response_stream_connection_failed: HttpFailureWire | None = None
    response_stream_disconnected: HttpFailureWire | None = None
    response_too_many_failed_attempts: HttpFailureWire | None = None
    active_turn_not_steerable: OpaqueJson | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class TurnErrorWire:
    message: str
    codex_error_info: OpaqueJson | None = None

@dataclass(frozen=True, kw_only=True)
class ErrorKindNameWire:
    kind: str

@dataclass(frozen=True, kw_only=True)
class TurnWire:
    id: str
    status: str
    error: TurnErrorWire | None = None

@dataclass(frozen=True, kw_only=True)
class TurnParamsWire:
    thread_id: str
    turn: TurnWire

@dataclass(frozen=True, kw_only=True)
class DeltaParamsWire:
    thread_id: str
    turn_id: str
    item_id: str
    delta: str

@dataclass(frozen=True, kw_only=True)
class ItemWire:
    type: str
    id: str
    text: str | None = None

@dataclass(frozen=True, kw_only=True)
class ItemParamsWire:
    thread_id: str
    turn_id: str
    item: OpaqueJson

@dataclass(frozen=True, kw_only=True)
class CommandExecutionWire:
    command: str
    cwd: str
    status: str
    aggregated_output: str | None = None
    exit_code: int | None = None

@dataclass(frozen=True, kw_only=True)
class PatchKindWire:
    type: str
    move_path: str | None = None

@dataclass(frozen=True, kw_only=True)
class FileUpdateWire:
    path: str
    kind: PatchKindWire
    diff: str

@dataclass(frozen=True, kw_only=True)
class FileChangeWire:
    changes: tuple[FileUpdateWire, ...]
    status: str

@dataclass(frozen=True, kw_only=True)
class WebSearchActionWire:
    type: str
    queries: tuple[str, ...] | None = None
    url: str | None = None
    pattern: str | None = None

@dataclass(frozen=True, kw_only=True)
class WebSearchWire:
    query: str
    action: WebSearchActionWire | None = None

@dataclass(frozen=True, kw_only=True)
class ErrorParamsWire:
    thread_id: str
    turn_id: str
    will_retry: bool
    error: TurnErrorWire

@dataclass(frozen=True, kw_only=True)
class TokenCountWire:
    total_tokens: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None

@dataclass(frozen=True, kw_only=True)
class TokenUsageWire:
    total: TokenCountWire
    last: TokenCountWire
    model_context_window: int | None = None

@dataclass(frozen=True, kw_only=True)
class TokenUsageParamsWire:
    thread_id: str
    turn_id: str
    token_usage: TokenUsageWire

@dataclass(frozen=True, kw_only=True)
class RateLimitWindowWire:
    used_percent: int
    window_duration_mins: int | None = None
    resets_at: int | None = None

@dataclass(frozen=True, kw_only=True)
class RateLimitSnapshotWire:
    limit_id: str | None = None
    primary: RateLimitWindowWire | None = None
    secondary: RateLimitWindowWire | None = None

@dataclass(frozen=True, kw_only=True)
class RateLimitsParamsWire:
    rate_limits: RateLimitSnapshotWire

@dataclass(frozen=True, kw_only=True)
class UnknownValue:
    value: OpaqueJson

@dataclass(frozen=True, kw_only=True)
class Response:
    id: int | str
    thread_id: str | None = None
    turn_id: str | None = None

@dataclass(frozen=True, kw_only=True)
class ErrorResponse:
    id: int | str
    code: int
    message: str

@dataclass(frozen=True, kw_only=True)
class ServerRequest:
    id: int | str
    method: str
    params: OpaqueJson | None = None

@dataclass(frozen=True, kw_only=True)
class ThreadStarted:
    thread_id: str

@dataclass(frozen=True, kw_only=True)
class TurnStarted:
    thread_id: str
    turn_id: str

@dataclass(frozen=True, kw_only=True)
class TextDelta:
    thread_id: str
    turn_id: str
    item_id: str
    text: str

@dataclass(frozen=True, kw_only=True)
class ReasoningDelta:
    thread_id: str
    turn_id: str
    item_id: str
    text: str
    summary: bool

@dataclass(frozen=True, kw_only=True)
class AgentMessageDone:
    thread_id: str
    turn_id: str
    item_id: str
    text: str

@dataclass(frozen=True, kw_only=True)
class CommandRun:
    command: str
    cwd: str
    status: ToolStatus | UnknownValue
    output: str | None = None
    exit_code: int | None = None

@dataclass(frozen=True, kw_only=True)
class FileEdit:
    path: str
    kind: str
    diff: str
    move_path: str | None = None

@dataclass(frozen=True, kw_only=True)
class FilePatch:
    changes: tuple[FileEdit, ...]
    status: ToolStatus | UnknownValue

@dataclass(frozen=True, kw_only=True)
class WebSearch:
    query: str
    action: str | None = None
    queries: tuple[str, ...] = ...
    url: str | None = None
    pattern: str | None = None
ToolItem: TypeAlias = CommandRun | FilePatch | WebSearch

@dataclass(frozen=True, kw_only=True)
class ItemStarted:
    thread_id: str
    turn_id: str
    item_id: str
    item_type: str
    tool: ToolItem | None = None

@dataclass(frozen=True, kw_only=True)
class ItemDone:
    thread_id: str
    turn_id: str
    item_id: str
    item_type: str
    tool: ToolItem | None = None

@dataclass(frozen=True, kw_only=True)
class TokenCount:
    total_tokens: int | None = None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None

@dataclass(frozen=True, kw_only=True)
class TokenUsage:
    thread_id: str
    turn_id: str
    last: TokenCount
    total: TokenCount
    model_context_window: int | None = None

@dataclass(frozen=True, kw_only=True)
class TurnError:
    thread_id: str
    turn_id: str
    message: str
    error_kind: str | UnknownValue | None
    http_status: int | None
    will_retry: bool

@dataclass(frozen=True, kw_only=True)
class UsageLimitHit:
    minutes: int | None
    resets_at: int | None
    text: str

@dataclass(frozen=True, kw_only=True)
class TurnEnded:
    thread_id: str
    turn_id: str
    status: TurnStatus | UnknownValue
    error_message: str | None = None
    error_kind: str | UnknownValue | None = None
    http_status: int | None = None
    usage_limit: UsageLimitHit | None = None

@dataclass(frozen=True, kw_only=True)
class RateLimitWindow:
    used_percent: int
    minutes: int | None
    resets_at: int | None

@dataclass(frozen=True, kw_only=True)
class RateLimits:
    limit_id: str | None
    primary: RateLimitWindow | None
    secondary: RateLimitWindow | None

@dataclass(frozen=True, kw_only=True)
class Other:
    method: str

@dataclass(frozen=True, kw_only=True)
class Unparsed:
    method: str
    reason: str
CodexLine: TypeAlias = Response | ErrorResponse | ServerRequest | ThreadStarted | TurnStarted | TextDelta | ReasoningDelta | AgentMessageDone | ItemStarted | ItemDone | TokenUsage | RateLimits | TurnError | TurnEnded | Other | Unparsed

@dataclass(frozen=True, kw_only=True)
class ErrorFacts:
    kind: str | UnknownValue | None
    http_status: int | None

def reason_of(malformed: Malformed) -> _Program[str, object]:
    ...

def error_facts_of(info: OpaqueJson | None) -> _Program[ErrorFacts, object]:
    ...

def known_error_facts_of(info: ErrorInfoWire) -> _Program[ErrorFacts, object]:
    ...

def token_count_of(wire: TokenCountWire) -> _Program[TokenCount, object]:
    ...

def turn_status_of(spelled: str) -> _Program[TurnStatus | UnknownValue, object]:
    ...

def turn_ended_of(params: TurnParamsWire) -> _Program[TurnEnded, object]:
    ...

def turn_error_of(params: ErrorParamsWire) -> _Program[TurnError, object]:
    ...

def token_usage_of(params: TokenUsageParamsWire) -> _Program[TokenUsage, object]:
    ...

def window_of(window: RateLimitWindowWire | None) -> _Program[RateLimitWindow | None, object]:
    ...

def rate_limits_of(params: RateLimitsParamsWire) -> _Program[RateLimits, object]:
    ...

def tool_status_of(spelled: str) -> _Program[ToolStatus | UnknownValue, object]:
    ...

def command_run_of(wire: CommandExecutionWire) -> _Program[CommandRun, object]:
    ...

def file_patch_of(wire: FileChangeWire) -> _Program[FilePatch, object]:
    ...

def web_search_of(wire: WebSearchWire) -> _Program[WebSearch, object]:
    ...

def tool_parsed(method: str, wire_type: type, convert: Callable, item: OpaqueJson) -> _Program[ToolItem | Unparsed, object]:
    ...

def tool_of(method: str, item_type: str, item: OpaqueJson) -> _Program[ToolItem | Unparsed | None, object]:
    ...

def item_started_of(params: ItemParamsWire) -> _Program[ItemStarted | Unparsed, object]:
    ...

def item_done_of(params: ItemParamsWire) -> _Program[AgentMessageDone | ItemDone | Unparsed, object]:
    ...

def text_delta_of(params: DeltaParamsWire) -> _Program[TextDelta, object]:
    ...

def reasoning_text_of(params: DeltaParamsWire) -> _Program[ReasoningDelta, object]:
    ...

def reasoning_summary_of(params: DeltaParamsWire) -> _Program[ReasoningDelta, object]:
    ...

def thread_started_of(params: ThreadParamsWire) -> _Program[ThreadStarted, object]:
    ...

def turn_started_of(params: TurnParamsWire) -> _Program[TurnStarted, object]:
    ...

def parsed_into(method: str, wire_type: type, convert: Callable, params: OpaqueJson | None) -> _Program[CodexLine, object]:
    ...

def notification_of(method: str, params: OpaqueJson | None) -> _Program[CodexLine, object]:
    ...

def response_of(envelope: EnvelopeWire) -> _Program[Response | Unparsed, object]:
    ...

def classify_line(line: str) -> _Program[CodexLine, object]:
    ...
