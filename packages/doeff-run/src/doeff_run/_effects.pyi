# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _effects.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from enum import StrEnum as StrEnum
from doeff_run._values import RUN_NAME as RUN_NAME
from doeff_run._values import RunSpec as RunSpec
from doeff_run._values import RunHandle as RunHandle
from doeff_run._values import RunPending as RunPending
from doeff_run._values import RunLive as RunLive
from doeff_run._values import RunStopped as RunStopped
from doeff_run._values import RunGone as RunGone
from doeff_run._values import RunState as RunState

@dataclass(frozen=True, kw_only=True)
class RunAccepted:
    handle: RunHandle

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class RunNameInUse:
    handle: RunHandle

    def __post_init__(self) -> None:
        ...

class RefusalWhy(StrEnum):
    NO_CAPACITY = 'no-capacity'
    UNSUPPORTED = 'unsupported'

@dataclass(frozen=True, kw_only=True)
class RunRefused:
    name: str
    why: RefusalWhy
    detail: str = ''

    def __post_init__(self) -> None:
        ...
RunStartOutcome: TypeAlias = RunAccepted | RunNameInUse | RunRefused

@dataclass(frozen=True, kw_only=True)
class StopFailed:
    handle: RunHandle
    detail: str = ''

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class RunStillAlive:
    handle: RunHandle

    def __post_init__(self) -> None:
        ...
RunStopOutcome: TypeAlias = RunStopped | RunGone | StopFailed
RunRemoveOutcome: TypeAlias = RunGone | RunStillAlive

@dataclass(frozen=True, kw_only=True)
class RunEvent:
    seq: int
    at: datetime
    state: RunLive | RunStopped | RunGone

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class RunEventPage:
    events: tuple[RunEvent, ...]
    next_seq: int
    state: RunState

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class RunStart(_doeff_effect_base[RunStartOutcome]):
    name: str
    spec: RunSpec

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class RunStop(_doeff_effect_base[RunStopOutcome]):
    handle: RunHandle
    grace_seconds: float

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class RunRemove(_doeff_effect_base[RunRemoveOutcome]):
    handle: RunHandle

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class RunStatus(_doeff_effect_base[RunState]):
    handle: RunHandle

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class RunReadEvents(_doeff_effect_base[RunEventPage]):
    handle: RunHandle
    after_seq: int
    wait_up_to: float

    def __post_init__(self) -> None:
        ...
