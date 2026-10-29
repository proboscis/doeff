# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _request_timing.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_core_effects import SecondsObservation as SecondsObservation
from doeff_records._wire import OPERATIONS as OPERATIONS
from doeff_records._wire import SERVER_TIMING_NAMES as SERVER_TIMING_NAMES
from doeff_records._wire import ServerSpan as ServerSpan
from doeff_records._wire import server_timing_spelled as server_timing_spelled
from doeff_records._pg_sql import ROWS_UNIT as ROWS_UNIT
from doeff_records._pg_sql import stream_unit as stream_unit
from doeff_records._pg_sql import table_unit as table_unit

class RequestMark(StrEnum):
    STARTED = 'started'
    READ = 'read'
    HANDLER_IN = 'handler-in'
    HANDLER_OUT = 'handler-out'
    WAIT_IN = 'wait-in'
    WOKE = 'woke'
    DECIDED = 'decided'
    SENT = 'sent'

class RequestStage(StrEnum):
    QUEUE = 'queue'
    BODY = 'body'
    DECODE = 'decode'
    HANDLER = 'handler'
    WAIT = 'wait'
    ENCODE = 'encode'
    SEND = 'send'
    TOTAL = 'total'
    WOKE = 'woke'
STAGE_METRIC: str

@dataclass(frozen=True, kw_only=True)
class MarkAt:
    mark: RequestMark
    at: float

@dataclass(frozen=True, kw_only=True)
class StageSeconds:
    stage: RequestStage
    seconds: float

@dataclass(frozen=True, kw_only=True)
class WaitSum:
    seconds: float
    last_woke: float | None

def stage_metric(operation: str, stage: RequestStage) -> _Program[str, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class StageMeaning:
    stage: RequestStage
    meaning: str
STAGE_MEANINGS: tuple[StageMeaning, ...]
STAGE_METRIC_NAMES: FrozenMap
STAGE_METRIC_HELPS: FrozenMap

def first_at(marks: tuple[MarkAt, ...], mark: RequestMark) -> _Program[float | None, object]:
    ...

def waited(marks: tuple[MarkAt, ...]) -> _Program[WaitSum, object]:
    ...

def between(since: float | None, until: float | None) -> _Program[float | None, object]:
    ...
SERVER_TIMING_STAGES: tuple[RequestStage, ...]

def server_timing_spans_of(stages: tuple[StageSeconds, ...]) -> _Program[tuple[ServerSpan, ...], object]:
    ...

def server_timing_of(stages: tuple[StageSeconds, ...]) -> _Program[str, object]:
    ...

def request_stages(received_at: float | None, marks: tuple[MarkAt, ...], wait: WaitSum) -> _Program[tuple[StageSeconds, ...], object]:
    ...

def sent_stages(received_at: float | None, marks: tuple[MarkAt, ...], wait: WaitSum, sent_at: float) -> _Program[tuple[StageSeconds, ...], object]:
    ...

class LockPhase(StrEnum):
    WAIT = 'wait'
    HOLD = 'hold'
LOCK_METRIC: str
TABLE_LOCK_OPERATIONS: tuple[str, ...]
ROWS_LOCK_OPERATIONS: tuple[str, ...]
STREAM_LOCK_OPERATIONS: tuple[str, ...]

def metric_word(text: str) -> _Program[str, object]:
    ...

def lock_metric(operation: str, unit: str, phase: LockPhase) -> _Program[str, object]:
    ...

def lock_observations(operation: str, units: tuple, waited: float, held: float) -> _Program[tuple[SecondsObservation, ...], object]:
    ...
LOCK_PHASE_MEANINGS: tuple[tuple[LockPhase | str, ...], ...]

def lock_metric_helps(tables: tuple, streams: tuple) -> _Program[FrozenMap[str], object]:
    ...
