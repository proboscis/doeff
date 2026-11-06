# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = job_reach_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff import EffectBase as EffectBase
from typing import ClassVar as ClassVar
from doeff_cluster._shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster._shared.intent.record_spec import RecordMode as RecordMode
from doeff_cluster._shared.intent.job_call_model import JobRequest as JobRequest

class JobWay(StrEnum):
    BY_NAME = 'by-name'
    BY_ADDRESS = 'by-address'

class ReachFailure(StrEnum):
    NOT_CONNECTED = 'not-connected'
    TIMED_OUT = 'timed-out'
    OTHER = 'other'

@dataclass(frozen=True, kw_only=True)
class JobReached:
    status: int

@dataclass(frozen=True, kw_only=True)
class JobNotReached:
    failure: ReachFailure
    reason: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class JobNoWay:
    reason: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ReachJob(EffectBase[JobReached | JobNotReached | JobNoWay]):
    __record_spec__: ClassVar[RecordSpec]
    api: str
    request: JobRequest
    way: JobWay
    connect_patience: float | None

class ResolveFailure(StrEnum):
    NOT_RESOLVED = 'not-resolved'
    TIMED_OUT = 'timed-out'

@dataclass(frozen=True, kw_only=True)
class JobResolved:
    ...

@dataclass(frozen=True, kw_only=True)
class JobNotResolved:
    failure: ResolveFailure
    reason: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ResolveJob(EffectBase[JobResolved | JobNotResolved | JobNoWay]):
    __record_spec__: ClassVar[RecordSpec]
    api: str
    patience: float
