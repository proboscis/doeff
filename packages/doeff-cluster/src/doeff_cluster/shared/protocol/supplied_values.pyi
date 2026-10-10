# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = supplied_values.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from urllib.parse import quote as url_quote
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_hy.wire import Malformed as Malformed
from doeff_hy.wire import parse_json as parse_json
from doeff_core_effects.effects import Ask as Ask
from doeff_core_effects.file_effects import AcquireLock as AcquireLock
from doeff_core_effects.file_effects import DirEntry as DirEntry
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import ListDirectory as ListDirectory
from doeff_core_effects.file_effects import LockHeld as LockHeld
from doeff_core_effects.file_effects import MakeDirectory as MakeDirectory
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import ReleaseLock as ReleaseLock
from doeff_core_effects.file_effects import RemoveTree as RemoveTree
from doeff_core_effects.file_effects import WriteBytes as WriteBytes
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import ReadEnvironment as ReadEnvironment
from doeff_cluster.shared.intent.supplied_model import SuppliedKind as SuppliedKind
from doeff_cluster.shared.intent.supplied_model import SuppliedObjectRef as SuppliedObjectRef
from doeff_cluster.shared.intent.supplied_model import SuppliedRef as SuppliedRef
from doeff_cluster.shared.intent.supplied_model import SuppliedValueUnavailable as SuppliedValueUnavailable
from doeff_cluster.shared.intent.supplied_model import WorkerFactMissing as WorkerFactMissing
from doeff_cluster.shared.intent.supplied_model import WorkerFactName as WorkerFactName
KUBE_API: str
SERVICE_ACCOUNT_DIR: str
SERVICE_ACCOUNT_TOKEN_FILE: str
SERVICE_ACCOUNT_CA_FILE: str
FACT_ENVIRON_NAMES: FrozenMap
REF_SPELLING: Incomplete
OBJECT_SPELLING: Incomplete
FILE_KEY: Incomplete
FILE_MODE: int
DIRECTORY_MODE: int
REFUSAL_BODY_LIMIT: int
REQUEST_TIMEOUT_SECONDS: float

def text_field(value: str) -> _Program[str, object]:
    ...

def status_field(value: int) -> _Program[int, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class HeldData:
    data: dict[str, str] | None = None

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
