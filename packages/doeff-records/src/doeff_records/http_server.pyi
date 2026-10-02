"""http_server.hy の公開面の型(型検査のための宣言 — 実行時は http_server.hy を読む)。

http_server.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、入口の設定 RecordsServing・走っている木 ServedBuild・
検の殻 start-records-server が Unknown になる(使い手 — 記録の service の入口の組み立てと模擬の土台 — の strict の型検査で、書き手に
直せない赤が連なる・#2742)。ここで型を宣言する。

- `(defrecord …)` は kw_only の frozen の dataclass、`(defclass [(dataclass :frozen True)] …)` は位置でも渡せる frozen の dataclass。
- `(defeffect …)` は答えの型を持つ EffectBase の frozen の dataclass。
- defk は呼ぶと Program を返す(答えの型 = 実装の :post の型)。
- handler-for は「書き手の名 → その書き手の記録の handler」。handler の形は置き場ごとに違うので、答えは object に留める(service.pyi と
  同じ扱い)。readiness・pressure・meter も実装の注記と同じ素の Callable。
- HttpAddress の module(doeff_core_effects.http_server_effects)には型の宣言がまだ無い。
- 実装との食い違いは packages/doeff-records/tests/test_static_stubs.py が名・欄の名と順・既定値の有無・引数の名で検める。
"""

from collections.abc import Callable
from dataclasses import dataclass

from doeff_core_effects.http_server_effects import HttpAddress
from doeff_records.service import HttpAnswer, HttpRequest
from doeff_records.values import RecordsSchema

from doeff import EffectBase, Program

REQUEST_MAX_BYTES: int

@dataclass(frozen=True, kw_only=True)
class MaintenancePlan:
    interval_seconds: float
    keep_seconds: float

@dataclass(frozen=True, kw_only=True)
class RepoCommit:
    repo: str
    commit: str

@dataclass(frozen=True, kw_only=True)
class ServedBuild:
    commits: tuple[RepoCommit, ...] | None = None
    instance: str | None = None

@dataclass(frozen=True, kw_only=True)
class RecordsServing:
    address: HttpAddress
    schema: RecordsSchema
    prepare: Program[object, object] | EffectBase[object]
    request_handlers: tuple[object, ...]
    max_bytes: int
    maintenance: MaintenancePlan | None
    stop_poll_seconds: float
    drain_seconds: float
    readiness: Callable[..., object] | None = None
    pressure: Callable[..., object] | None = None
    meter: Callable[..., object] | None = None
    served: ServedBuild | None = None

@dataclass(frozen=True, kw_only=True)
class TextAnswer:
    status: int
    content_type: str
    body: str

@dataclass(frozen=True)
class RecordsListening(EffectBase[None]):
    address: HttpAddress

@dataclass(frozen=True)
class PreparedHandlers(EffectBase[Callable[[str], object] | None]): ...

def answer_with(serving: RecordsServing, request: HttpRequest) -> Program[HttpAnswer | TextAnswer, object]: ...
def serve_records(serving: RecordsServing) -> Program[int, object]: ...
def ready_handlers(handler_for: Callable[[str], object]) -> Program[Callable[[str], object], object]: ...

@dataclass(frozen=True)
class RecordsServerConfig:
    schema: RecordsSchema
    handler_for: Callable[[str], object]
    request_handlers: tuple[object, ...] = ()
    host: str = "127.0.0.1"
    port: int = 0
    meter: Callable[..., object] | None = None
    served: ServedBuild | None = None

def records_server_config(
    schema: RecordsSchema,
    handler_for: Callable[[str], object],
    request_handlers: tuple[object, ...] = (),
    host: str = "127.0.0.1",
    port: int = 0,
    meter: Callable[..., object] | None = None,
    served: ServedBuild | None = None,
) -> Program[RecordsServerConfig, object]: ...

@dataclass(frozen=True)
class RunningServer:
    url: str
    stop: Callable[[], None]
    def close(self) -> None: ...

def start_records_server(config: RecordsServerConfig) -> RunningServer: ...
