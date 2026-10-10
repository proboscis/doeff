# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = k8s_supplied.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from httpx import ConnectError as ConnectError
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpFailureKind as HttpFailureKind
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_cluster.shared.protocol.supplied_values import KUBE_API as KUBE_API
from doeff_cluster.shared.protocol.supplied_values import OBJECT_SPELLING as OBJECT_SPELLING
from doeff_cluster.shared.protocol.supplied_values import REF_SPELLING as REF_SPELLING
from doeff_cluster.shared.protocol.supplied_values import SERVICE_ACCOUNT_TOKEN_FILE as SERVICE_ACCOUNT_TOKEN_FILE
from doeff_cluster.shared.protocol.supplied_values import text_field as text_field
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
UNREACHABLE: str
OBJECT_PATH: Incomplete
KIND_OF_RESOURCE: dict[str, str]
RESOURCE_OF_KIND: dict[str, str]

@dataclass(frozen=True, kw_only=True)
class HeldEntry:
    key: str
    value: str

@dataclass(frozen=True, kw_only=True)
class HeldObject:
    resource: str
    namespace: str
    name: str
    entries: tuple[HeldEntry, ...]

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class ReadGrant:
    resource: str
    namespace: str
    name: str | None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class SuppliedCluster:
    token: str
    objects: tuple[HeldObject, ...]
    grants: tuple[ReadGrant, ...]
    down: bool = False

def held_value_of(spelled: str, value: str) -> _Program[HeldObject, object]:
    ...

def held_whole_of(spelled: str, entries: tuple[HeldEntry, ...]) -> _Program[HeldObject, object]:
    ...

def read_grant_of(held: HeldObject) -> _Program[ReadGrant, object]:
    ...

def service_account_token_file(token: str) -> _Handler:
    ...

def api_answer(status: int, text: str, url: str) -> _Program[HttpResponse, object]:
    ...

def status_text(code: int, reason: str, message: str) -> _Program[str, object]:
    ...

def object_text(held: HeldObject) -> _Program[str, object]:
    ...

def answer_of(cluster: SuppliedCluster, presented: str | None, namespace: str, resource: str, name: str, url: str) -> _Program[HttpResponse, object]:
    ...

def headers_field(value: dict) -> _Program[dict, object]:
    ...

def k8s_supplied_peer(cluster: SuppliedCluster) -> _Handler:
    ...
