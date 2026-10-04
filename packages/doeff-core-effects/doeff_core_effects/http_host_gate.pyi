# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = http_host_gate.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from urllib.parse import urlsplit as urlsplit
from doeff_core_effects.http_effects import HttpFailed as HttpFailed
from doeff_core_effects.http_effects import HttpFailureKind as HttpFailureKind
from doeff_core_effects.http_effects import HttpRequest as HttpRequest
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

class HostNotAllowed(ConnectionError):
    url: str
    hosts: frozenset[str]

    def __init__(self, url: str, hosts: frozenset[str]) -> None:
        ...

def http_host_gate(hosts: frozenset[str]) -> _Handler:
    ...
