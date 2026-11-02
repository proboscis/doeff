# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = workspace_volumes.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from doeff_cluster._shared.intent.workspace_volume_model import AttachWorkspaceVolume as AttachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import CreateWorkspaceVolume as CreateWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import DetachWorkspaceVolume as DetachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import MountWorkspaceVolume as MountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import UmountWorkspaceVolume as UmountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceOutcome as WorkspaceOutcome
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceVolumeRefused as WorkspaceVolumeRefused
from doeff_cluster._shared.core.workspace_volume_rules import owner_refusal as owner_refusal
from doeff_cluster._shared.core.workspace_volume_rules import volume_name as volume_name

@dataclass(frozen=True, kw_only=True)
class HeldVolume:
    name: str
    node: str | None = None
    mounted_at: str | None = None
    formatted: bool = False

class WorkspaceVolumeWorld:
    node: str
    volumes: tuple[HeldVolume, ...]
    busy: frozenset
    down: bool

    def __init__(self, node: str, volumes: tuple=..., busy: frozenset=..., down: bool=False) -> None:
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

def sim_created(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_attached(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_mounted(world: WorkspaceVolumeWorld, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_unmounted(world: WorkspaceVolumeWorld, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def sim_detached(world: WorkspaceVolumeWorld, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def held_workspace_volumes(world: WorkspaceVolumeWorld) -> _Handler:
    ...
