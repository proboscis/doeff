# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = kube_client.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
import threading as threading
from typing import Callable as Callable
import httpx as httpx
from doeff_hy.json_value import OpaqueJson as OpaqueJson
from doeff_core_effects.offloaded_call import run_detached as run_detached
SA_DIR: str
API_URL: str
WATCH_READ_MARGIN_SECONDS: int

@dataclass(frozen=True, kw_only=True)
class KubeSettings:
    fail: Callable[[str], Exception]
    base: str
    sa_dir: str
    timeout: float
    transport: httpx.BaseTransport | None
    retry_seconds: float
    watch_seconds: int
    client: httpx.Client

@dataclass(frozen=True, kw_only=True)
class WatchNext:
    version: str | None
    pause: float

@dataclass(frozen=True, kw_only=True)
class WatchPlace:
    settings: KubeSettings
    list_url: str
    name: str
    kind: str
    key: str
    on_body: Callable[[OpaqueJson], None]
    on_error: Callable[[str], None]
    stopped: threading.Event
    held: list
    http: httpx.Client

@dataclass(frozen=True, kw_only=True)
class KubeClient:
    settings: KubeSettings
    follow: Callable[[str, str, Callable[[OpaqueJson], None], Callable[[str], None]], Callable[[], None]]
    follow_node: Callable[[str, Callable[[OpaqueJson], None], Callable[[str], None]], Callable[[], None]]
    scale: Callable[[str, str, int, bool], int]
    annotate: Callable[[str, str, dict], None]

def keepalive_options() -> _Program[tuple, object]:
    ...

def kube_available(sa_dir: str=...) -> _Program[bool, object]:
    ...

def new_pool(sa_dir: str, timeout: float, transport: httpx.BaseTransport | None) -> _Program[httpx.Client, object]:
    ...

def kube_headers(settings: KubeSettings, content_type: str | None) -> _Program[dict, object]:
    ...

def deployments_url(settings: KubeSettings, namespace: str) -> _Program[str, object]:
    ...

def nodes_url(settings: KubeSettings) -> _Program[str, object]:
    ...

def deployment_url(settings: KubeSettings, namespace: str, name: str, sub: str) -> _Program[str, object]:
    ...

def kube_call(settings: KubeSettings, method: str, url: str, content_type: str | None, dry_run: bool, content: bytes | None) -> _Program[dict, object]:
    ...

def scale_patch(replicas: int) -> _Program[dict, object]:
    ...

def annotate_patch(annotations: dict) -> _Program[dict, object]:
    ...

def scale_deployment(settings: KubeSettings, namespace: str, name: str, replicas: int, dry_run: bool) -> _Program[int, object]:
    ...

def annotate_deployment(settings: KubeSettings, namespace: str, name: str, annotations: dict) -> _Program[None, object]:
    ...

def watch_place(settings: KubeSettings, list_url: str, name: str, kind: str, key: str, on_body: Callable[[OpaqueJson], None], on_error: Callable[[str], None]) -> _Program[WatchPlace, object]:
    ...

def stop_watch(watching: WatchPlace) -> _Program[None, object]:
    ...

def tell_body(watching: WatchPlace, body: dict) -> _Program[None, object]:
    ...

def tell_error(watching: WatchPlace, reason: str) -> _Program[None, object]:
    ...

def version_of(body: dict | list | str | int | float | bool | None) -> _Program[str | None, object]:
    ...

def watch_params(watching: WatchPlace) -> _Program[dict, object]:
    ...

def watch_query(watching: WatchPlace, version: str) -> _Program[dict, object]:
    ...

def list_once(watching: WatchPlace) -> _Program[str | None, object]:
    ...

def watch_once(watching: WatchPlace, version: str) -> _Program[WatchNext, object]:
    ...

def tell_unexpected(watching: WatchPlace, error: Exception) -> _Program[None, object]:
    ...

def watch_run(watching: WatchPlace) -> _Program[None, object]:
    ...

def start_watch(watching: WatchPlace) -> _Program[Callable[[], None], object]:
    ...

def follow_deployment(settings: KubeSettings, namespace: str, name: str, on_body: Callable[[OpaqueJson], None], on_error: Callable[[str], None]) -> _Program[Callable[[], None], object]:
    ...

def follow_node_object(settings: KubeSettings, name: str, on_body: Callable[[OpaqueJson], None], on_error: Callable[[str], None]) -> _Program[Callable[[], None], object]:
    ...

def kube_client(fail: Callable[[str], Exception], base: str=..., sa_dir: str=..., timeout: float=5.0, transport: httpx.BaseTransport | None=None, retry_seconds: float=10.0, watch_seconds: int=60) -> _Program[KubeClient, object]:
    ...
