# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = workspace_volume_rules.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
NAME_PREFIX: str
NAME_MAX_LENGTH: int
OWNER_SPELLING: Incomplete
TICKET_TYPE: str
DEVICE_DIR_NAME: str
MOUNTINFO_ESCAPE: Incomplete
USED_AT_KEY: str
COOLING_TICKET_PREFIXES: tuple[str, ...]
RESTORE_TICKET_PREFIX: str
COOLING_MARK: str
GROWING_MARK: str
BACKUP_COMPLETED: str
BUNDLE_HASH_LENGTH: int

class AttachStep(StrEnum):
    PUT = 'put'
    WAIT = 'wait'
    READY = 'ready'
    ELSEWHERE = 'elsewhere'

class DetachStep(StrEnum):
    REMOVE = 'remove'
    WAIT = 'wait'
    GONE = 'gone'
    ELSEWHERE = 'elsewhere'

@dataclass(frozen=True, kw_only=True)
class MountEntry:
    mount_point: str
    source: str
    fs_type: str

@dataclass(frozen=True, kw_only=True)
class AttachView:
    attachment: bool
    ticket_node: str | None
    satisfied: bool
    message: str
    state: str
    current_node: str
    other_tickets: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class BackupView:
    name: str
    state: str
    created_at: str
    url: str
    volume_size: int = 0

@dataclass(frozen=True, kw_only=True)
class DiskRoom:
    maximum: int
    reserved: int
    scheduled: int
    available: int
    node: str = ''
    disk: str = ''

@dataclass(frozen=True, kw_only=True)
class RoomBytes:
    nominal: int
    actual: int

@dataclass(frozen=True, kw_only=True)
class WarmVolume:
    name: str
    used_at: float
    size: int = 0
    actual: int = 0

@dataclass(frozen=True, kw_only=True)
class CoolingPlan:
    names: tuple[str, ...]
    covered: bool

def owner_refusal(owner: str) -> _Program[str | None, object]:
    ...

def volume_name(owner: str) -> _Program[str, object]:
    ...

def ticket_id(name: str) -> _Program[str, object]:
    ...

def _unescaped(text: str) -> _Program[str, object]:
    ...

def mount_entries(text: str) -> _Program[tuple[MountEntry, ...], object]:
    ...

def mounted_at(entries: tuple[MountEntry, ...], path: str) -> _Program[MountEntry | None, object]:
    ...

def volume_of_entry(entry: MountEntry) -> _Program[str | None, object]:
    ...

def volume_mount_points(entries: tuple[MountEntry, ...], name: str) -> _Program[tuple[str, ...], object]:
    ...

def attach_step(view: AttachView, node: str) -> _Program[AttachStep, object]:
    ...

def detach_step(view: AttachView, node: str) -> _Program[DetachStep, object]:
    ...

def using_tickets(ids: tuple[str, ...]) -> _Program[tuple[str, ...], object]:
    ...

def cooling_name(name: str, stamp: int) -> _Program[str, object]:
    ...

def growing_name(name: str, stamp: int) -> _Program[str, object]:
    ...

def newest_backup(backups: tuple[BackupView, ...]) -> _Program[BackupView | None, object]:
    ...

def backup_volume_name(name: str, target: str) -> _Program[str, object]:
    ...

def floor_bytes(room: DiskRoom, minimal: int, floors: dict[str, int]) -> _Program[int, object]:
    ...

def room_bytes(rooms: tuple[DiskRoom, ...], percentage: int, minimal: int, floors: dict[str, int]) -> _Program[RoomBytes, object]:
    ...

def hyx_room_coversXquestion_markX(room: RoomBytes, demand: int) -> _Program[bool, object]:
    ...

def cooling_order(warm: tuple[WarmVolume, ...], now: float, warm_seconds: float, room: RoomBytes, demand: int) -> _Program[CoolingPlan, object]:
    ...

def restore_node(rooms: tuple[DiskRoom, ...], percentage: int, minimal: int, floors: dict[str, int], demand: int) -> _Program[str, object]:
    ...
