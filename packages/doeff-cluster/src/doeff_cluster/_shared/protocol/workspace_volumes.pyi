# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = workspace_volumes.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from datetime import datetime as datetime
from urllib.parse import quote as url_quote
from doeff_hy._wire import Malformed as Malformed
from doeff_hy._wire import parse_json as parse_json
from doeff_hy._wire import parse_json_as as parse_json_as
from doeff_core_effects import DiskUsage as DiskUsage
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ListDirectory as ListDirectory
from doeff_core_effects import MakeDirectory as MakeDirectory
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import PathStat as PathStat
from doeff_core_effects import ReadDiskUsage as ReadDiskUsage
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import RemoveTree as RemoveTree
from doeff_core_effects import RenamePath as RenamePath
from doeff_core_effects import StatPath as StatPath
from doeff_core_effects import WriteText as WriteText
from doeff_core_effects import HttpFailed as HttpFailed
from doeff_core_effects import HttpRequest as HttpRequest
from doeff_core_effects import HttpResponse as HttpResponse
from doeff_time import Delay as Delay
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import GetTime as GetTime
from doeff_cluster._shared.intent.workspace_volume_model import AttachWorkspaceVolume as AttachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import CoolIdleWorkspaceVolumes as CoolIdleWorkspaceVolumes
from doeff_cluster._shared.intent.workspace_volume_model import CoolingSweep as CoolingSweep
from doeff_cluster._shared.intent.workspace_volume_model import CoolWorkspaceVolume as CoolWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import CreateWorkspaceVolume as CreateWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import DetachWorkspaceVolume as DetachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import MountWorkspaceVolume as MountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import PurgeWorkspaceVolume as PurgeWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import ReadWorkspacePlace as ReadWorkspacePlace
from doeff_cluster._shared.intent.workspace_volume_model import ReadWorkspaceRoom as ReadWorkspaceRoom
from doeff_cluster._shared.intent.workspace_volume_model import UmountWorkspaceVolume as UmountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceOutcome as WorkspaceOutcome
from doeff_cluster._shared.intent.workspace_volume_model import WorkspacePlace as WorkspacePlace
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceRoom as WorkspaceRoom
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceRoomShort as WorkspaceRoomShort
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceVolumeRefused as WorkspaceVolumeRefused
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceVolumeSite as WorkspaceVolumeSite
from doeff_cluster._shared.intent.supplied_model import WorkerFactName as WorkerFactName
from doeff_cluster._shared.core.workspace_volume_rules import AttachStep as AttachStep
from doeff_cluster._shared.core.workspace_volume_rules import AttachView as AttachView
from doeff_cluster._shared.core.workspace_volume_rules import BackupView as BackupView
from doeff_cluster._shared.core.workspace_volume_rules import DetachStep as DetachStep
from doeff_cluster._shared.core.workspace_volume_rules import DiskRoom as DiskRoom
from doeff_cluster._shared.core.workspace_volume_rules import MountEntry as MountEntry
from doeff_cluster._shared.core.workspace_volume_rules import NAME_PREFIX as NAME_PREFIX
from doeff_cluster._shared.core.workspace_volume_rules import USED_AT_KEY as USED_AT_KEY
from doeff_cluster._shared.core.workspace_volume_rules import WarmVolume as WarmVolume
from doeff_cluster._shared.core.workspace_volume_rules import attach_step as attach_step
from doeff_cluster._shared.core.workspace_volume_rules import cooling_name as cooling_name
from doeff_cluster._shared.core.workspace_volume_rules import cooling_order as cooling_order
from doeff_cluster._shared.core.workspace_volume_rules import detach_step as detach_step
from doeff_cluster._shared.core.workspace_volume_rules import free_slots as free_slots
from doeff_cluster._shared.core.workspace_volume_rules import mount_entries as mount_entries
from doeff_cluster._shared.core.workspace_volume_rules import mounted_at as mounted_at
from doeff_cluster._shared.core.workspace_volume_rules import newest_backup as newest_backup
from doeff_cluster._shared.core.workspace_volume_rules import owner_refusal as owner_refusal
from doeff_cluster._shared.core.workspace_volume_rules import ticket_id as ticket_id
from doeff_cluster._shared.core.workspace_volume_rules import TICKET_TYPE as TICKET_TYPE
from doeff_cluster._shared.core.workspace_volume_rules import using_tickets as using_tickets
from doeff_cluster._shared.core.workspace_volume_rules import volume_mount_points as volume_mount_points
from doeff_cluster._shared.core.workspace_volume_rules import volume_name as volume_name
from doeff_cluster._shared.core.workspace_volume_rules import volume_of_entry as volume_of_entry
from doeff_cluster._shared.protocol.supplied_values import KUBE_API as KUBE_API
from doeff_cluster._shared.protocol.supplied_values import REFUSAL_BODY_LIMIT as REFUSAL_BODY_LIMIT
from doeff_cluster._shared.protocol.supplied_values import REQUEST_TIMEOUT_SECONDS as REQUEST_TIMEOUT_SECONDS
from doeff_cluster._shared.protocol.supplied_values import SERVICE_ACCOUNT_TOKEN_FILE as SERVICE_ACCOUNT_TOKEN_FILE
from doeff_cluster._shared.protocol.supplied_values import kube_headers as kube_headers
from doeff_cluster._shared.protocol.supplied_values import service_account_text as service_account_text
from doeff_cluster._shared.protocol.supplied_values import status_field as status_field
from doeff_cluster._shared.protocol.supplied_values import text_field as text_field
from doeff_cluster._shared.protocol.supplied_values import worker_fact as worker_fact
WORKSPACE_SITE: WorkspaceVolumeSite
LONGHORN_API: str
SETTINGS_KEY: str
JSON_TYPE: str
MERGE_PATCH_TYPE: str
CLASS_PARAMETERS: frozenset[str]
CLASS_FS_TYPE: str

@dataclass(frozen=True, kw_only=True)
class SettingsWire:
    storage_class: str
    size_bytes: int
    warm_hours: int
    spare_volumes: int
    keep_days: int
    grow_below_percent: int = 0
    max_size_bytes: int = 0

@dataclass(frozen=True, kw_only=True)
class ConfigMapWire:
    data: dict[str, str] | None = None

@dataclass(frozen=True, kw_only=True)
class StorageClassWire:
    parameters: dict[str, str] | None = None

@dataclass(frozen=True, kw_only=True)
class RecurringSelectorWire:
    name: str
    is_group: bool = False

@dataclass(frozen=True, kw_only=True)
class VolumeConditionWire:
    type: str = ''
    status: str = ''
    reason: str = ''
    message: str = ''

@dataclass(frozen=True, kw_only=True)
class VolumeStatusWire:
    state: str = ''
    current_node_id: str = ''
    restore_required: bool = False
    restore_initiated: bool = False
    conditions: tuple[VolumeConditionWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class VolumeMetaWire:
    name: str = ''
    resource_version: str = ''
    annotations: dict[str, str] | None = None
    creation_timestamp: str = ''
    deletion_timestamp: str | None = None

@dataclass(frozen=True, kw_only=True)
class VolumeSpecWire:
    from_backup: str = ''
    backup_target_name: str = ''
    size: str = ''

@dataclass(frozen=True, kw_only=True)
class EngineStatusWire:
    current_size: str = ''

@dataclass(frozen=True, kw_only=True)
class EngineWire:
    status: EngineStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class EngineListWire:
    items: tuple[EngineWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class VolumeWire:
    metadata: VolumeMetaWire | None = None
    spec: VolumeSpecWire | None = None
    status: VolumeStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class VolumeListWire:
    items: tuple[VolumeWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class ReplicaSpecWire:
    node_id: str = ''

@dataclass(frozen=True, kw_only=True)
class ReplicaWire:
    spec: ReplicaSpecWire | None = None

@dataclass(frozen=True, kw_only=True)
class ReplicaListWire:
    items: tuple[ReplicaWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class NamedMetaWire:
    name: str = ''

@dataclass(frozen=True, kw_only=True)
class BackupStatusWire:
    state: str = ''
    backup_created_at: str = ''
    url: str = ''
    error: str = ''
    volume_size: str = ''

@dataclass(frozen=True, kw_only=True)
class BackupWire:
    metadata: NamedMetaWire | None = None
    status: BackupStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class BackupListWire:
    items: tuple[BackupWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class NamedWire:
    metadata: NamedMetaWire | None = None

@dataclass(frozen=True, kw_only=True)
class NamedListWire:
    items: tuple[NamedWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class SnapshotStatusWire:
    ready_to_use: bool = False
    error: str = ''

@dataclass(frozen=True, kw_only=True)
class SnapshotWire:
    status: SnapshotStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class DiskSpecWire:
    tags: tuple[str, ...] | None = None
    allow_scheduling: bool = False
    storage_reserved: int = 0

@dataclass(frozen=True, kw_only=True)
class DiskConditionWire:
    type: str = ''
    status: str = ''

@dataclass(frozen=True, kw_only=True)
class DiskStatusWire:
    storage_maximum: int = 0
    storage_scheduled: int = 0
    storage_available: int = 0
    conditions: tuple[DiskConditionWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class NodeSpecWire:
    disks: dict[str, DiskSpecWire] | None = None

@dataclass(frozen=True, kw_only=True)
class NodeStatusWire:
    disk_status: dict[str, DiskStatusWire] | None = None

@dataclass(frozen=True, kw_only=True)
class NodeWire:
    spec: NodeSpecWire | None = None
    status: NodeStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class NodeListWire:
    items: tuple[NodeWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class LonghornSettingWire:
    value: str

@dataclass(frozen=True, kw_only=True)
class TicketWire:
    node_id: str = ''

@dataclass(frozen=True, kw_only=True)
class TicketConditionWire:
    type: str = ''
    message: str = ''

@dataclass(frozen=True, kw_only=True)
class TicketStatusWire:
    satisfied: bool = False
    conditions: tuple[TicketConditionWire, ...] | None = None

@dataclass(frozen=True, kw_only=True)
class AttachmentMetaWire:
    resource_version: str

@dataclass(frozen=True, kw_only=True)
class AttachmentSpecWire:
    attachment_tickets: dict[str, TicketWire] | None = None

@dataclass(frozen=True, kw_only=True)
class AttachmentStatusWire:
    attachment_ticket_statuses: dict[str, TicketStatusWire] | None = None

@dataclass(frozen=True, kw_only=True)
class AttachmentWire:
    metadata: AttachmentMetaWire
    spec: AttachmentSpecWire | None = None
    status: AttachmentStatusWire | None = None

@dataclass(frozen=True, kw_only=True)
class Placing:
    failed: bool
    note: str

@dataclass(frozen=True, kw_only=True)
class AttachRead:
    view: AttachView
    version: str
    placing: Placing

@dataclass(frozen=True, kw_only=True)
class KeeperAnswer:
    code: int
    notes: str

@dataclass(frozen=True, kw_only=True)
class VolumeRead:
    name: str
    version: str
    state: str
    restoring: bool
    deleting: bool
    used_at: float
    backup_target: str
    placing: Placing
    size: int = 0

def refused(owner: str, doing: str, reason: str) -> _Program[WorkspaceVolumeRefused, object]:
    ...

def checked_name(owner: str, doing: str) -> _Program[str, object]:
    ...

def kube_answer(owner: str, doing: str, method: str, path: str, body: str | None, content_type: str) -> _Program[HttpResponse, object]:
    ...

def status_refusal(owner: str, doing: str, what: str, answer: HttpResponse) -> _Program[WorkspaceVolumeRefused, object]:
    ...

def malformed_refusal(owner: str, doing: str, what: str) -> _Program[WorkspaceVolumeRefused, object]:
    ...

def volume_path(site: WorkspaceVolumeSite, name: str) -> _Program[str, object]:
    ...

def attachment_path(site: WorkspaceVolumeSite, name: str) -> _Program[str, object]:
    ...

def workspace_settings(site: WorkspaceVolumeSite, owner: str) -> _Program[SettingsWire, object]:
    ...

def class_parameters(owner: str, class_name: str) -> _Program[dict[str, str], object]:
    ...

def volume_create_text(site: WorkspaceVolumeSite, name: str, parameters: dict[str, str], size_bytes: int, from_backup: str) -> _Program[str, object]:
    ...

def longhorn_path(site: WorkspaceVolumeSite, kind: str) -> _Program[str, object]:
    ...

def epoch_of(text: str) -> _Program[float | None, object]:
    ...

def placing_of(status: VolumeStatusWire) -> _Program[Placing, object]:
    ...

def volume_read_of(owner: str, doing: str, held: VolumeWire) -> _Program[VolumeRead, object]:
    ...

def volume_read(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[VolumeRead | None, object]:
    ...

def waited_gone(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[None, object]:
    ...

def waited_restored(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[None, object]:
    ...

def label_query(name: str, target: str) -> _Program[str, object]:
    ...

def backup_view_of(item: BackupWire) -> _Program[BackupView, object]:
    ...

def backup_views(site: WorkspaceVolumeSite, owner: str, doing: str, name: str, target: str) -> _Program[tuple[BackupView, ...], object]:
    ...

def class_backup_target(owner: str, doing: str, parameters: dict[str, str]) -> _Program[str, object]:
    ...

def room_short(owner: str, doing: str, reason: str, fresh: bool) -> _Program[WorkspaceRoomShort, object]:
    ...

def class_disk_tags(parameters: dict[str, str]) -> _Program[tuple[str, ...], object]:
    ...

def room_slots(site: WorkspaceVolumeSite, owner: str, doing: str, parameters: dict[str, str], size_bytes: int) -> _Program[int, object]:
    ...

def room_held(site: WorkspaceVolumeSite, owner: str, doing: str, parameters: dict[str, str], size_bytes: int, fresh: bool) -> _Program[None, object]:
    ...

def replicas_placed(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[bool, object]:
    ...

def dropped_unplaced(site: WorkspaceVolumeSite, owner: str, doing: str, name: str, note: str, fresh: bool) -> _Program[None, object]:
    ...

def created_volume(site: WorkspaceVolumeSite, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def marked_volume(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[bool, object]:
    ...

def attach_read(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[AttachRead | None, object]:
    ...

def ticket_put_text(name: str, node: str, version: str) -> _Program[str, object]:
    ...

def ticket_removal_text(name: str, version: str) -> _Program[str, object]:
    ...

def patched_attachment(site: WorkspaceVolumeSite, owner: str, doing: str, name: str, body: str) -> _Program[bool, object]:
    ...

def attached_volume(site: WorkspaceVolumeSite, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def mount_table(site: WorkspaceVolumeSite, owner: str, doing: str) -> _Program[tuple[MountEntry, ...], object]:
    ...

def detached_volume(site: WorkspaceVolumeSite, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def keeper_answer_of(text: str) -> _Program[KeeperAnswer | None, object]:
    ...

def keeper_answer(site: WorkspaceVolumeSite, owner: str, doing: str, verb: str, name: str, path: str) -> _Program[KeeperAnswer, object]:
    ...

def checked_path(owner: str, doing: str, path: str) -> _Program[str, object]:
    ...

def place_filled(owner: str, doing: str, target: str) -> _Program[bool, object]:
    ...

def owner_held(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[bool, object]:
    ...

def place_read(site: WorkspaceVolumeSite, owner: str, path: str) -> _Program[WorkspacePlace, object]:
    ...

def mounted_volume(site: WorkspaceVolumeSite, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def unmounted_volume(site: WorkspaceVolumeSite, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def created_object(site: WorkspaceVolumeSite, owner: str, doing: str, kind: str, body: str) -> _Program[None, object]:
    ...

def snapshot_taken(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[None, object]:
    ...

def backup_finished(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[None, object]:
    ...

def deleted_volume(site: WorkspaceVolumeSite, owner: str, doing: str, name: str, version: str) -> _Program[bool, object]:
    ...

def tickets_of(name: str, view: AttachView) -> _Program[tuple[str, ...], object]:
    ...

def engine_size(site: WorkspaceVolumeSite, owner: str, doing: str, name: str) -> _Program[int, object]:
    ...

def waited_engine(site: WorkspaceVolumeSite, owner: str, doing: str, name: str, wanted: int) -> _Program[None, object]:
    ...

def grown_volume(site: WorkspaceVolumeSite, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class Growth:
    grown: tuple[str, ...]
    failed: tuple[str, ...]

def mounted_growth(site: WorkspaceVolumeSite) -> _Program[Growth, object]:
    ...

def cooled_volume(site: WorkspaceVolumeSite, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def backup_volume_names(site: WorkspaceVolumeSite, owner: str, doing: str, name: str, target: str) -> _Program[tuple[str, ...], object]:
    ...

def deleted_object(site: WorkspaceVolumeSite, owner: str, doing: str, kind: str, name: str) -> _Program[None, object]:
    ...

def purged_volume(site: WorkspaceVolumeSite, owner: str) -> _Program[WorkspaceOutcome, object]:
    ...

def volume_reads(site: WorkspaceVolumeSite, owner: str, doing: str) -> _Program[tuple[VolumeRead, ...], object]:
    ...

def disk_rooms(site: WorkspaceVolumeSite, owner: str, doing: str, tags: tuple[str, ...]) -> _Program[tuple[DiskRoom, ...], object]:
    ...

def longhorn_percentage(site: WorkspaceVolumeSite, owner: str, doing: str, setting: str) -> _Program[int, object]:
    ...

def workspace_room(site: WorkspaceVolumeSite) -> _Program[WorkspaceRoom, object]:
    ...

def idle_cooling(site: WorkspaceVolumeSite) -> _Program[CoolingSweep, object]:
    ...

def workspace_volumes_api(site: WorkspaceVolumeSite) -> _Handler:
    ...
