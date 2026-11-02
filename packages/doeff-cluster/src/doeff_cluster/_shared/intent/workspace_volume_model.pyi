# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = workspace_volume_model.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from typing import ClassVar as ClassVar
from doeff import EffectBase as EffectBase
from doeff_cluster._shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster._shared.intent.record_spec import RecordMode as RecordMode

class WorkspaceOutcome(StrEnum):
    DONE = 'done'
    ALREADY = 'already'

@dataclass(frozen=True, kw_only=True)
class WorkspaceVolumeSite:
    longhorn_namespace: str
    settings_namespace: str
    settings_name: str
    mount_dir: str
    mountinfo: str
    attach_seconds: float
    detach_seconds: float
    mount_seconds: float
    poll_seconds: float

    def __post_init__(self) -> None:
        ...

class WorkspaceVolumeRefused(RuntimeError):
    ...

@dataclass(frozen=True)
class CreateWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str

@dataclass(frozen=True)
class AttachWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str

@dataclass(frozen=True)
class MountWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str
    path: str

@dataclass(frozen=True)
class UmountWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str
    path: str

@dataclass(frozen=True)
class DetachWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str
