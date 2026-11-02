# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = workspace_volumes.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from urllib.parse import quote as url_quote
from doeff_hy._wire import Malformed as Malformed
from doeff_hy._wire import parse_json as parse_json
from doeff_hy._wire import parse_json_as as parse_json_as
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import MakeDirectory as MakeDirectory
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import PathStat as PathStat
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
from doeff_cluster._shared.intent.workspace_volume_model import AttachWorkspaceVolume as AttachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import CreateWorkspaceVolume as CreateWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import DetachWorkspaceVolume as DetachWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import MountWorkspaceVolume as MountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import UmountWorkspaceVolume as UmountWorkspaceVolume
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceOutcome as WorkspaceOutcome
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceVolumeRefused as WorkspaceVolumeRefused
from doeff_cluster._shared.intent.workspace_volume_model import WorkspaceVolumeSite as WorkspaceVolumeSite
from doeff_cluster._shared.intent.supplied_model import WorkerFactName as WorkerFactName
from doeff_cluster._shared.core.workspace_volume_rules import AttachStep as AttachStep
from doeff_cluster._shared.core.workspace_volume_rules import AttachView as AttachView
from doeff_cluster._shared.core.workspace_volume_rules import DetachStep as DetachStep
from doeff_cluster._shared.core.workspace_volume_rules import MountEntry as MountEntry
from doeff_cluster._shared.core.workspace_volume_rules import attach_step as attach_step
from doeff_cluster._shared.core.workspace_volume_rules import detach_step as detach_step
from doeff_cluster._shared.core.workspace_volume_rules import mount_entries as mount_entries
from doeff_cluster._shared.core.workspace_volume_rules import mounted_at as mounted_at
from doeff_cluster._shared.core.workspace_volume_rules import owner_refusal as owner_refusal
from doeff_cluster._shared.core.workspace_volume_rules import ticket_id as ticket_id
from doeff_cluster._shared.core.workspace_volume_rules import TICKET_TYPE as TICKET_TYPE
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
class VolumeStatusWire:
    state: str = ''
    current_node_id: str = ''

@dataclass(frozen=True, kw_only=True)
class VolumeWire:
    status: VolumeStatusWire | None = None

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
class AttachRead:
    view: AttachView
    version: str

@dataclass(frozen=True, kw_only=True)
class KeeperAnswer:
    code: int
    notes: str

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

def volume_create_text(site: WorkspaceVolumeSite, name: str, parameters: dict[str, str], size_bytes: int) -> _Program[str, object]:
    ...

def created_volume(site: WorkspaceVolumeSite, owner: str) -> _Program[WorkspaceOutcome, object]:
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

def mounted_volume(site: WorkspaceVolumeSite, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def unmounted_volume(site: WorkspaceVolumeSite, owner: str, path: str) -> _Program[WorkspaceOutcome, object]:
    ...

def workspace_volumes_api(site: WorkspaceVolumeSite) -> _Handler:
    ...
