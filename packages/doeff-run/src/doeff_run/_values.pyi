# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _values.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from enum import StrEnum as StrEnum
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_hy._frozen import frozen_map_of as frozen_map_of
RUN_NAME: Incomplete
VOLUME_NAME: Incomplete
ENV_NAME: Incomplete
IMAGE_NAME: Incomplete

@dataclass(frozen=True, kw_only=True)
class RunHandle:
    name: str
    run_id: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class ResourceLimits:
    cpu_millis: int | None = None
    memory_bytes: int | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class VolumeMount:
    source: str
    target: str
    read_only: bool = False

    def __post_init__(self) -> None:
        ...

def volume_name_refusal(name: str) -> str | None:
    ...

def checked_env(value: object) -> FrozenMap[str]:
    ...

@dataclass(frozen=True, kw_only=True)
class RunSpec:
    image: str
    command: tuple[str, ...] = ...
    env: FrozenMap[str] = ...
    volumes: tuple[VolumeMount, ...] = ...
    limits: ResourceLimits = ...

    def __post_init__(self) -> None:
        ...

class StopCause(StrEnum):
    EXITED = 'exited'
    REQUESTED = 'requested'
    OUT_OF_MEMORY = 'out-of-memory'
    NEVER_BEGAN = 'never-began'

@dataclass(frozen=True)
class RunPending:
    ...

@dataclass(frozen=True)
class RunLive:
    ...

@dataclass(frozen=True, kw_only=True)
class RunStopped:
    exit_code: int | None
    cause: StopCause
    detail: str = ''

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class RunGone:
    detail: str = ''

    def __post_init__(self) -> None:
        ...
RunState: TypeAlias = RunPending | RunLive | RunStopped | RunGone
