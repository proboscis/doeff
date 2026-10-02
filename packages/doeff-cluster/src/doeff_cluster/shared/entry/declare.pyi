# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = declare.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from collections.abc import Mapping as Mapping
from urllib.parse import quote as url_quote
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff_core_effects.http_effects import HttpResponse as HttpResponse
from doeff_cluster.shared.protocol.declaration_requests import spec_for_update as spec_for_update
from doeff_cluster.shared.protocol.declaration_requests import create_body as create_body
from doeff_cluster.shared.intent.service_model import Declaration as Declaration
DECLARE_REPLY_SECONDS: float

def declare_request(method: str, url: str, actor: str, body: dict[str, object] | None) -> _Program[HttpResponse, object]:
    ...

def service_written(base: str, actor: str, row: Mapping[str, object], replicas: int | None) -> _Program[HttpResponse, object]:
    ...

def apply_declaration(url: str, declaration: Declaration, actor: str, replicas: int | None=None) -> _Program[bool, object]:
    ...
