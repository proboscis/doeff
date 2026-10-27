# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = http_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff import EffectBase as EffectBase
_HTTP_METHODS: frozenset[str]

class HttpRequest(EffectBase):
    method: str
    url: str
    headers: dict[str, str] | None
    params: dict[str, object] | None
    body: bytes | str | dict[str, object] | None
    timeout_seconds: float
    max_retries: int
    follow_redirects: bool
    failures_as_values: bool
    log_each_request: bool
    connect_timeout_seconds: float | None
    trace_phases: bool

    def __init__(self, method: str, url: str, *, headers: dict[str, str] | None=None, params: Mapping[str, object] | None=None, body: bytes | str | Mapping[str, object] | None=None, timeout_seconds: float=30.0, max_retries: int=3, follow_redirects: bool=True, failures_as_values: bool=False, log_each_request: bool=False, connect_timeout_seconds: float | None=None, trace_phases: bool=False) -> None:
        ...

    def __repr__(self) -> str:
        ...

@dataclass(frozen=True, kw_only=True)
class HttpPhases:
    new_connection: bool
    connect_seconds: float
    acquire_seconds: float
    send_seconds: float
    wait_seconds: float
    receive_seconds: float

    def __post_init__(self) -> None:
        ...

class HttpResponse:
    status: int
    headers: dict[str, str]
    content: bytes
    text: str
    url: str
    elapsed_seconds: float
    phases: HttpPhases | None

    def __init__(self, status: int, headers: dict[str, str], content: bytes, text: str, url: str, elapsed_seconds: float, *, phases: HttpPhases | None=None) -> None:
        ...

    def raise_for_status(self) -> None:
        ...

class HttpError(Exception):
    status: int
    url: str
    body_snippet: str

    def __init__(self, status: int, url: str, body_snippet: str) -> None:
        ...

class HttpFailureKind(StrEnum):
    TIMED_OUT = 'timed-out'
    CONNECT_FAILED = 'connect-failed'
    OTHER = 'other'

@dataclass(frozen=True, kw_only=True)
class HttpFailed:
    url: str
    detail: str
    kind: HttpFailureKind
