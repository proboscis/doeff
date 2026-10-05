# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = request_timing.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_records.wire import OPERATIONS as OPERATIONS

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
STAGE_METRIC_HELPS: FrozenMap

def first_at(marks: tuple[MarkAt, ...], mark: RequestMark) -> _Program[float | None, object]:
    ...

def waited(marks: tuple[MarkAt, ...]) -> _Program[WaitSum, object]:
    ...

def between(since: float | None, until: float | None) -> _Program[float | None, object]:
    ...

def request_stages(received_at: float | None, marks: tuple[MarkAt, ...]) -> _Program[tuple[StageSeconds, ...], object]:
    ...
