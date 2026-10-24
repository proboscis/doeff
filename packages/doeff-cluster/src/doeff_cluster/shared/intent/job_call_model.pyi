# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = job_call_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from typing import ClassVar as ClassVar
from doeff_cluster.shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster.shared.intent.record_spec import RecordMode as RecordMode

@dataclass(frozen=True, kw_only=True)
class JobField:
    name: str
    value: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class JobRequest:
    method: str
    path: str
    query: tuple[JobField, ...] = ...
    headers: tuple[JobField, ...] = ...
    body: bytes | None = None
    patience: float = 30.0

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class JobAnswer:
    status: int
    content_type: str
    body: bytes
    headers: tuple[JobField, ...] = ...

@dataclass(frozen=True, kw_only=True)
class JobUnreachable:
    reason: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class CallJob(EffectBase[JobAnswer | JobUnreachable]):
    __record_spec__: ClassVar[RecordSpec]
    api: str
    request: JobRequest
