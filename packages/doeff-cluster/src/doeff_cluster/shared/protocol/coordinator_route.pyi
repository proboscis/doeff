# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = coordinator_route.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpFailureKind as HttpFailureKind
from doeff_time import Delay as Delay
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
ROUND_PAUSE_SECONDS: float

@dataclass(frozen=True, kw_only=True)
class CoordinatorRoute:
    urls: tuple[str, ...]
    active: int
    switched_at_ms: int

@dataclass(frozen=True, kw_only=True)
class RouteOptions:
    reply_seconds: float
    watch_seconds: float
    connect_seconds: float
    connect_retries: int
    recheck_ms: int
    actor: str
    resend_deadline_seconds: float
    resend_pause_seconds: float

@dataclass(frozen=True, kw_only=True)
class RouteTurn:
    indices: tuple[int, ...]
    route: CoordinatorRoute

class RouteCell:
    route: CoordinatorRoute

    def __init__(self, route: CoordinatorRoute) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class RoutedReply:
    answer: HttpResponse | HttpFailed
    route: CoordinatorRoute

def route_of(spec: str, now_ms: int) -> _Program[CoordinatorRoute, object]:
    ...

def route_order(route: CoordinatorRoute, now_ms: int, recheck_ms: int) -> _Program[RouteTurn, object]:
    ...

def route_used(route: CoordinatorRoute, index: int, now_ms: int) -> _Program[CoordinatorRoute, object]:
    ...

def round_pause_seconds(round: int) -> _Program[float, object]:
    ...

def routed_request(route: CoordinatorRoute, method: str, path: str, options: RouteOptions, params: dict | None, body: dict | list | None) -> _Program[RoutedReply, object]:
    ...

def resent_request(route: CoordinatorRoute, method: str, path: str, options: RouteOptions, params: dict | None, body: dict | list | None, deadline_seconds: float, pause_seconds: float) -> _Program[RoutedReply, object]:
    ...

class RouteRefused(Exception):
    status: int
    body: str

    def __init__(self, status: int, body: str) -> None:
        ...

    def __repr__(self) -> str:
        ...

class RouteUnreachable(Exception):
    failed: HttpFailed

    def __init__(self, failed: HttpFailed) -> None:
        ...

    def __repr__(self) -> str:
        ...

def answer_json(answer: HttpResponse | HttpFailed | None) -> _Program[dict | list | str | int | float | bool | None, object]:
    ...

def write_accepted(answer: HttpResponse | HttpFailed | None) -> _Program[bool, object]:
    ...
