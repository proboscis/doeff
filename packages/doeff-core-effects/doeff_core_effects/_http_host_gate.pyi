# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _http_host_gate.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff_hy._static_types import Handler as _Handler
from urllib.parse import urlsplit as urlsplit
from doeff_core_effects._http_effects import HttpFailed as HttpFailed
from doeff_core_effects._http_effects import HttpFailureKind as HttpFailureKind
from doeff_core_effects._http_effects import HttpRequest as HttpRequest

class HostNotAllowed(ConnectionError):
    url: str
    hosts: frozenset[str]

    def __init__(self, url: str, hosts: frozenset[str]) -> None:
        ...

def http_host_gate(hosts: frozenset[str]) -> _Handler:
    ...
