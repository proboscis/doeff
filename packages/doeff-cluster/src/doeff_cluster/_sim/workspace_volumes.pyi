# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = workspace_volumes.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from datetime import datetime as datetime
from doeff_time import GetTime as GetTime
from doeff_cluster._shared.intent.workspace_volume_model import AttachedWorkspaceVolume as AttachedWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import AttachWorkspaceVolume as AttachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import CoolIdleWorkspaceVolumes as CoolIdleWorkspaceVolumes
from doeff_cluster._shared.intent.workspace_volume_model import CoolingSweep as CoolingSweep
from doeff_cluster._shared.intent.workspace_volume_model import CoolWorkspaceVolume as CoolWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import CreateWorkspaceVolume as CreateWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import DetachWorkspaceVolume as DetachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import MountWorkspaceVolume as MountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import PurgeWorkspaceVolume as PurgeWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import ReadAttachedWorkspaceVolumes as ReadAttachedWorkspaceVolumes
from doeff_cluster._shared.intent.workspace_volume_model import ReadWorkspacePlace as ReadWorkspacePlace
from doeff_cluster._shared.intent.workspace_volume_model import ReadWorkspaceRoom as ReadWorkspaceRoom
from doeff_cluster._shared.intent.workspace_volume_model import UmountWorkspaceVolume as UmountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceOutcome as WorkspaceOutcome
from doeff_cluster._shared.intent.workspace_volume_model import WorkspacePlace as WorkspacePlace
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceClusterUnreachable as WorkspaceClusterUnreachable
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceRoom as WorkspaceRoom
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceRoomShort as WorkspaceRoomShort
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceVolumeRefused as WorkspaceVolumeRefused
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ListDirectory as ListDirectory
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import PathStat as PathStat
from doeff_core_effects import StatPath as StatPath
from doeff_cluster._shared.core.workspace_volume_rules import NAME_PREFIX as NAME_PREFIX
from doeff_cluster._shared.core.workspace_volume_rules import CoolingPlan as CoolingPlan
from doeff_cluster._shared.core.workspace_volume_rules import RoomBytes as RoomBytes
from doeff_cluster._shared.core.workspace_volume_rules import WarmVolume as WarmVolume
from doeff_cluster._shared.core.workspace_volume_rules import cooling_name as cooling_name
from doeff_cluster._shared.core.workspace_volume_rules import cooling_order as cooling_order
from doeff_cluster._shared.core.workspace_volume_rules import owner_refusal as owner_refusal
from doeff_cluster._shared.core.workspace_volume_rules import hyx_room_coversXquestion_markX as hyx_room_coversXquestion_markX
from doeff_cluster._shared.core.workspace_volume_rules import ticket_id as ticket_id
from doeff_cluster._shared.core.workspace_volume_rules import volume_name as volume_name

@dataclass(frozen=True, kw_only=True)
class HeldVolume:
    name: str
    node: str | None = None
    mounted_at: str | None = None
    formatted: bool = False
    used_at: float = 0.0
    size: int = 0
    actual: int = 0

@dataclass(frozen=True, kw_only=True)
class HeldBackup:
    volume: str
    name: str
    created_at: float
    state: str = 'Completed'
    size: int = 0
    actual: int = 0

class WorkspaceVolumeWorld:
    node: str
    volumes: tuple[HeldVolume, ...]
    backups: tuple[HeldBackup, ...]
    busy: frozenset
    down: bool
    capacity: int
    minimal: int
    warm_hours: int
    spare_bytes: int
    keep_days: int
    size_bytes: int

    def __init__(self, node: str, volumes: tuple[HeldVolume, ...]=..., busy: frozenset=..., down: bool=False, backups: tuple[HeldBackup, ...]=..., capacity: int=20, minimal: int=0, warm_hours: int=2, spare_bytes: int=17179869184, keep_days: int=7, size_bytes: int=2147483648) -> None:
        ...

def held_volume(world: WorkspaceVolumeWorld, name: str) -> _Program[HeldVolume | None, object]:
    ...

def put_volume(world: WorkspaceVolumeWorld, volume: HeldVolume) -> _Program[None, object]:
    ...

def sim_refused(owner: str, doing: str, reason: str) -> _Program[WorkspaceVolumeRefused, object]:
    ...

def sim_name(world: WorkspaceVolumeWorld, owner: str, doing: str, api: bool) -> _Program[str, object]:
    ...

def sim_target(owner: str, doing: str, path: str) -> _Program[str, object]:
    ...

def held_backups(world: WorkspaceVolumeWorld, name: str) -> _Program[tuple[HeldBackup, ...], object]:
    ...

def sim_now() -> _Program[float, object]:
    ...

def sim_created(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_room_bytes(world: WorkspaceVolumeWorld) -> _Program[RoomBytes, object]:
    ...

def sim_warm(world: WorkspaceVolumeWorld) -> _Program[tuple[WarmVolume, ...], object]:
    ...

def sim_room_made(world: WorkspaceVolumeWorld, owner: str, doing: str, demand: int, fresh: bool) -> _Program[None, object]:
    ...

def sim_attached(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_filled(target: str) -> _Program[bool, object]:
    ...

def sim_place(world: WorkspaceVolumeWorld, owner: str, path: str) -> _Program[WorkspacePlace, object]:
    ...

def sim_mounted(world: WorkspaceVolumeWorld, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_unmounted(world: WorkspaceVolumeWorld, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_detached(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_cooled(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_purged(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_idle_cooled(world: WorkspaceVolumeWorld) -> _Program[CoolingSweep, object]:
    ...

def sim_room(world: WorkspaceVolumeWorld) -> _Program[WorkspaceRoom, object]:
    ...

def sim_attached_volumes(world: WorkspaceVolumeWorld) -> _Program[tuple[AttachedWorkspaceVolume, ...], object]:
    ...

def held_workspace_volumes(world: WorkspaceVolumeWorld) -> _Handler:
    ...
