# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = supplied_values.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from urllib.parse import quote as url_quote
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_hy._wire import Malformed as Malformed
from doeff_hy._wire import parse_json as parse_json
from doeff_core_effects import Ask as Ask
from doeff_core_effects import slog as slog
from doeff_core_effects import AcquireLock as AcquireLock
from doeff_core_effects import DirEntry as DirEntry
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ListDirectory as ListDirectory
from doeff_core_effects import LockHeld as LockHeld
from doeff_core_effects import MakeDirectory as MakeDirectory
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import PathStat as PathStat
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import ReleaseLock as ReleaseLock
from doeff_core_effects import RemoveTree as RemoveTree
from doeff_core_effects import StatPath as StatPath
from doeff_core_effects import WriteBytes as WriteBytes
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects import EnvEntry as EnvEntry
from doeff_core_effects import ReadEnvironment as ReadEnvironment
from doeff_cluster.shared.intent.supplied_model import SuppliedKind as SuppliedKind
from doeff_cluster.shared.intent.supplied_model import SuppliedObjectRef as SuppliedObjectRef
from doeff_cluster.shared.intent.supplied_model import SuppliedRef as SuppliedRef
from doeff_cluster.shared.intent.supplied_model import SuppliedValueUnavailable as SuppliedValueUnavailable
from doeff_cluster.shared.intent.supplied_model import WorkerFactMissing as WorkerFactMissing
from doeff_cluster.shared.intent.supplied_model import WorkerFactName as WorkerFactName
from doeff_cluster.shared.core.supplied_spelling import OBJECT_SPELLING as OBJECT_SPELLING
from doeff_cluster.shared.core.supplied_spelling import REF_SPELLING as REF_SPELLING
from doeff_cluster.shared.core.clock import datetime_of_epoch_ms as datetime_of_epoch_ms
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
KUBE_API: str
SERVICE_ACCOUNT_DIR: str
SERVICE_ACCOUNT_TOKEN_FILE: str
SERVICE_ACCOUNT_CA_FILE: str
FACT_ENVIRON_NAMES: FrozenMap
FILE_KEY: Incomplete
FILE_MODE: int
DIRECTORY_MODE: int
REFUSAL_BODY_LIMIT: int
REQUEST_TIMEOUT_SECONDS: float
HELD_DIR_NAME: str
WORKER_SPELLING: Incomplete
REFUSED_STATUSES: tuple[int, ...]

def text_field(value: str) -> _Program[str, object]:
    ...

def status_field(value: int) -> _Program[int, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class HeldData:
    data: dict[str, str] | None = None

@dataclass(frozen=True, kw_only=True)
class HeldCopy:
    read_at_ms: int
    data: dict[str, str]

@dataclass(frozen=True, kw_only=True)
class CopyPlace:
    root: str
    worker: str

@dataclass(frozen=True, kw_only=True)
class NoCopy:
    why: str

@dataclass(frozen=True, kw_only=True)
class HoldTally:
    held: int
    kept: int
    dropped: int
    pruned: int

def worker_fact(name: WorkerFactName) -> _Program[str, object]:
    ...

def service_account_text(path: str) -> _Program[str | FileFailed, object]:
    ...

def kube_headers(token: str, content_type: str) -> _Program[dict[str, str], object]:
    ...

def supplied_ref_of(text: str) -> _Program[SuppliedRef, object]:
    ...

def supplied_object_ref_of(text: str) -> _Program[SuppliedObjectRef, object]:
    ...

def supplied_ref_text(ref: SuppliedRef) -> _Program[str, object]:
    ...

def object_path(object: SuppliedObjectRef) -> _Program[str, object]:
    ...

def held_bytes(object: SuppliedObjectRef, spelled: str, raw: str) -> _Program[bytes, object]:
    ...

def entries_of_answer(object: SuppliedObjectRef, spelled: str, answer: HttpResponse | HttpFailed) -> _Program[dict[str, bytes], object]:
    ...

def object_answer(object: SuppliedObjectRef) -> _Program[HttpResponse | HttpFailed | FileFailed, object]:
    ...

def object_text(object: SuppliedObjectRef) -> _Program[str, object]:
    ...

def held_copy_path(root: str, worker: str, object: SuppliedObjectRef) -> _Program[str, object]:
    ...

def copy_place() -> _Program[CopyPlace | NoCopy, object]:
    ...

def held_copy_entries(object: SuppliedObjectRef, spelled: str, detail: str) -> _Program[dict[str, bytes] | NoCopy, object]:
    ...

def held_entries(object: SuppliedObjectRef, spelled: str) -> _Program[dict[str, bytes], object]:
    ...

def supplied_value(ref: SuppliedRef) -> _Program[str, object]:
    ...

def supplied_setting(name: str) -> _Program[str, object]:
    ...

def written_entries(directory: str, entries: dict[str, bytes]) -> _Program[FileFailed | None, object]:
    ...

def supplied_directory(name: str) -> _Program[str, object]:
    ...

class HoldDone(StrEnum):
    HELD = 'held'
    KEPT = 'kept'
    DROPPED = 'dropped'

@dataclass(frozen=True, kw_only=True)
class HeldWant:
    object: SuppliedObjectRef
    keys: frozenset | None

def hyx_worker_spelledXquestion_markX(worker: str) -> _Program[bool, object]:
    ...

def held_wants(spellings: tuple) -> _Program[tuple[HeldWant, ...], object]:
    ...

def copy_text(read_at_ms: int, entries: dict[str, bytes]) -> _Program[str, object]:
    ...

def private_dirs(root: str, path: str) -> _Program[FileFailed | None, object]:
    ...

def hold_one(root: str, worker: str, want: HeldWant, read_at_ms: int) -> _Program[HoldDone, object]:
    ...

def listed_names(path: str, kind: PathKind) -> _Program[tuple[str, ...], object]:
    ...

def pruned_copies(base: str, wanted: frozenset) -> _Program[int, object]:
    ...

def hold_supplied(worker: str, spellings: tuple) -> _Program[HoldTally, object]:
    ...
