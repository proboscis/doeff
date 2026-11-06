# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = job_call_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase
from typing import ClassVar as ClassVar
from doeff_cluster._shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster._shared.intent.record_spec import RecordMode as RecordMode

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
    follow_redirects: bool = False

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
