# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = detached_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff import EffectBase as EffectBase
from doeff import Program as Program
from .runtime_env_model import EnvVar as EnvVar
DETACHED_DEFAULT_LEASE_SECONDS: float
DETACHED_DEFAULT_RETAIN_SECONDS: float

@dataclass(frozen=True)
class SubmitDetached(EffectBase):
    program: Program
    key: str
    needs: frozenset = ...
    name: str = ''
    lease_seconds: float = ...
    retain_seconds: float = ...
    environ: tuple[EnvVar, ...] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class AwaitDetached(EffectBase):
    key: str
    timeout_seconds: float | None = None

@dataclass(frozen=True)
class CancelDetached(EffectBase):
    key: str

@dataclass(frozen=True)
class ReleaseDetached(EffectBase):
    key: str

@dataclass(frozen=True)
class ReadRunners(EffectBase):
    ...

@dataclass(frozen=True)
class AwaitRunnersChange(EffectBase):
    after: int
    timeout_seconds: float = 1.0

@dataclass(frozen=True)
class AwaitServiceReady(EffectBase):
    name: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class DetachedSubmitted:
    key: str
    created: bool

@dataclass(frozen=True)
class DetachedSucceeded:
    value: object

@dataclass(frozen=True)
class DetachedFailed:
    kind: str
    message: str
    traceback: str
    error: object = None

@dataclass(frozen=True)
class DetachedLost:
    reason: str

@dataclass(frozen=True)
class DetachedCancelled:
    ...

@dataclass(frozen=True)
class DetachedVersionMismatch:
    detail: str
    diffs: tuple = ...
    env_key: str = ''

@dataclass(frozen=True)
class DetachedUnrunnable:
    detail: str

@dataclass(frozen=True)
class DetachedEnvUnavailable:
    kind: str
    detail: str
    retryable: bool

@dataclass(frozen=True)
class DetachedUnknown:
    key: str

@dataclass(frozen=True)
class DetachedPending:
    key: str
    phase: str
    runner: str = ''

@dataclass(frozen=True, kw_only=True)
class RunnerFact:
    name: str
    provides: tuple[str, ...]
    exclusive: tuple[str, ...]
    live: bool
    draining: bool
    task_room: int
    node: str = ''

@dataclass(frozen=True, kw_only=True)
class RunnersUnreachable:
    detail: str
RunnersAnswer: TypeAlias = tuple[RunnerFact, ...] | RunnersUnreachable

@dataclass(frozen=True, kw_only=True)
class ServiceFact:
    name: str
    replicas: int | None
    failures: int | None
    last_exit_code: int | None
    last_exit_at_ms: int | None
    revision: str | None = None

@dataclass(frozen=True, kw_only=True)
class ServicesUnreachable:
    detail: str
ServicesAnswer: TypeAlias = tuple[ServiceFact, ...] | ServicesUnreachable

@_doeff_dataclass(frozen=True)
class ReadServices(_doeff_effect_base[ServicesAnswer]):
    ...

@dataclass(frozen=True, kw_only=True)
class ServiceProcessWire:
    failures: int | None = None
    last_exit_code: int | None = None
    last_exit_at_ms: int | None = None

@dataclass(frozen=True, kw_only=True)
class ServiceRowStatusWire:
    process: ServiceProcessWire | None = None

@dataclass(frozen=True, kw_only=True)
class ServiceRowSpecWire:
    replicas: int | None = None
    revision: str | None = None

@dataclass(frozen=True, kw_only=True)
class ServiceRowWire:
    name: str
    spec: ServiceRowSpecWire | None = None
    status: ServiceRowStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class ServiceListWire:
    items: tuple[ServiceRowWire, ...]

@dataclass(frozen=True, kw_only=True)
class RunnersChange:
    revision: int
    changed: bool

@dataclass(frozen=True, kw_only=True)
class RunnersWatchMissing:
    detail: str
RunnersChangeAnswer: TypeAlias = RunnersChange | RunnersWatchMissing | RunnersUnreachable

@dataclass(frozen=True, kw_only=True)
class ServiceReady:
    name: str
    revision: int

@dataclass(frozen=True, kw_only=True)
class ServiceStatusWire:
    ready: str

@dataclass(frozen=True, kw_only=True)
class ServiceViewWire:
    status: ServiceStatusWire
DetachedOutcome: TypeAlias = DetachedSucceeded | DetachedFailed | DetachedLost | DetachedCancelled | DetachedVersionMismatch | DetachedUnrunnable | DetachedEnvUnavailable | DetachedUnknown

@dataclass(frozen=True, kw_only=True)
class DetachedUnreachable:
    detail: str
DetachedAwaited: TypeAlias = DetachedOutcome | DetachedPending | DetachedUnreachable
DetachedSubmitAnswer: TypeAlias = DetachedSubmitted | DetachedUnreachable

class DetachedRefused(Exception):
    status: int
    message: str

    def __init__(self, status: int, message: str) -> None:
        ...
OPEN_PHASES: tuple[str, ...]
WARMING_PHASE: str
