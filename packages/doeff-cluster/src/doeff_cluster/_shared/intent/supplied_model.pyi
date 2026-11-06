# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = supplied_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

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
    WORKER_NAME = 'worker-name'

class WorkerFactMissing(RuntimeError):
    ...
