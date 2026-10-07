# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = declare.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Mapping as Mapping
from urllib.parse import quote as url_quote
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_cluster.shared.protocol.declaration_requests import ServiceRead as ServiceRead
from doeff_cluster.shared.protocol.declaration_requests import CONFLICT_REREADS as CONFLICT_REREADS
from doeff_cluster.shared.protocol.declaration_requests import service_read as service_read
from doeff_cluster.shared.protocol.declaration_requests import reread_after_conflict as reread_after_conflict
from doeff_cluster.shared.protocol.declaration_requests import needed_programs as needed_programs
from doeff_cluster.shared.protocol.declaration_requests import body_of as body_of
from doeff_cluster.shared.intent.service_model import Declaration as Declaration
DECLARE_REPLY_SECONDS: float

def declare_request(method: str, url: str, actor: str, body: dict[str, object] | None) -> _Program[HttpResponse, object]:
    ...

def service_current(url: str, actor: str) -> _Program[dict | None, object]:
    ...

def service_read_at(base: str, actor: str, name: str, row: Mapping[str, object]) -> _Program[ServiceRead, object]:
    ...

def service_written(base: str, actor: str, read: ServiceRead) -> _Program[HttpResponse, object]:
    ...

def service_written_rereading(base: str, actor: str, read: ServiceRead) -> _Program[HttpResponse, object]:
    ...

def apply_declaration(url: str, declaration: Declaration, actor: str) -> _Program[bool, object]:
    ...
