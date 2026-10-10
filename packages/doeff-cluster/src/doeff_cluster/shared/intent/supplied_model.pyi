# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = supplied_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum

class SuppliedKind(StrEnum):
    CONFIGMAP = 'configmap'
    SECRET = 'secret'

@dataclass(frozen=True, kw_only=True)
class SuppliedObjectRef:
    kind: SuppliedKind
    namespace: str
    name: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class SuppliedRef:
    kind: SuppliedKind
    namespace: str
    name: str
    key: str

    def __post_init__(self) -> None:
        ...

class SuppliedValueUnavailable(RuntimeError):
    ...

class WorkerFactName(StrEnum):
    NODE_NAME = 'node-name'
    SYSTEMD_ROOT = 'systemd-root'
    WORK_ROOT = 'work-root'

class WorkerFactMissing(RuntimeError):
    ...
