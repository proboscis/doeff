# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = workspace_volume_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from typing import ClassVar as ClassVar
from doeff import EffectBase as EffectBase
from doeff_cluster._shared.intent.record_spec import RecordSpec as RecordSpec
from doeff_cluster._shared.intent.record_spec import RecordMode as RecordMode

class WorkspaceOutcome(StrEnum):
    DONE = 'done'
    ALREADY = 'already'
    RESTORED = 'restored'

class WorkspacePlace(StrEnum):
    MOUNTED = 'mounted'
    READY = 'ready'
    OUTSIDE = 'outside'
    CLASH = 'clash'

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
    backup_seconds: float
    restore_seconds: float
    poll_seconds: float

    def __post_init__(self) -> None:
        ...

class WorkspaceVolumeRefused(RuntimeError):
    ...

class WorkspaceClusterUnreachable(WorkspaceVolumeRefused):
    ...

class WorkspaceRoomShort(WorkspaceVolumeRefused):
    fresh: bool

    def __init__(self, message: str, fresh: bool) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class WorkspaceRoom:
    nominal: int
    actual: int
    spare: int

@dataclass(frozen=True, kw_only=True)
class AttachedWorkspaceVolume:
    owner: str
    used_at: float

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class CoolingSweep:
    nominal: int
    actual: int
    cooled: tuple[str, ...]
    failed: tuple[str, ...]
    grown: tuple[str, ...] = ...
    covered: bool = True

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

@dataclass(frozen=True)
class CoolWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str

@dataclass(frozen=True)
class PurgeWorkspaceVolume(EffectBase[WorkspaceOutcome]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str

@dataclass(frozen=True)
class ReadWorkspacePlace(EffectBase[WorkspacePlace]):
    __record_spec__: ClassVar[RecordSpec]
    owner: str
    path: str

@dataclass(frozen=True)
class ReadWorkspaceRoom(EffectBase[WorkspaceRoom]):
    __record_spec__: ClassVar[RecordSpec]

@dataclass(frozen=True)
class ReadAttachedWorkspaceVolumes(EffectBase[tuple[AttachedWorkspaceVolume, ...]]):
    __record_spec__: ClassVar[RecordSpec]

@dataclass(frozen=True)
class CoolIdleWorkspaceVolumes(EffectBase[CoolingSweep]):
    __record_spec__: ClassVar[RecordSpec]
