# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = cluster_control.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff_cluster.shared.intent.service_model import System as System

@dataclass(frozen=True, kw_only=True)
class ServiceReadiness:
    state: str
    reason: str

@_doeff_dataclass(frozen=True)
class Redeclare(_doeff_effect_base[tuple[str, ...]]):
    system: System
    environ: dict[str, dict[str, str]] | None = None

@_doeff_dataclass(frozen=True)
class ReadinessOf(_doeff_effect_base[ServiceReadiness]):
    name: str

@dataclass(frozen=True, kw_only=True)
class ReadinessWaitExpired:
    name: str
    state: str
    last: ServiceReadiness
    waited_seconds: float

@dataclass(frozen=True, kw_only=True)
class JobProcessSeen:
    job: str
    pid: int

@dataclass(frozen=True, kw_only=True)
class JobProcessWaitExpired:
    job: str
    excluding: tuple[int, ...]
    waited_seconds: float

@_doeff_dataclass(frozen=True)
class AwaitReadiness(_doeff_effect_base[ServiceReadiness | ReadinessWaitExpired]):
    name: str
    state: str
    timeout_seconds: float

@_doeff_dataclass(frozen=True)
class AwaitJobProcess(_doeff_effect_base[JobProcessSeen | JobProcessWaitExpired]):
    job: str
    excluding: tuple[int, ...]
    timeout_seconds: float

@_doeff_dataclass(frozen=True)
class Crash(_doeff_effect_base[int]):
    name: str

@_doeff_dataclass(frozen=True)
class KillWorker(_doeff_effect_base[int]):
    name: str

@_doeff_dataclass(frozen=True)
class StopWorker(_doeff_effect_base[None]):
    name: str

@_doeff_dataclass(frozen=True)
class StopCoordinator(_doeff_effect_base[None]):
    seconds: float

@_doeff_dataclass(frozen=True)
class CrashCoordinator(_doeff_effect_base[None]):
    seconds: float
