"""protocol.hy の公開面の型(coordinator・worker・記録の置き場が取り交わす形 — 型検査のための宣言・実行時は protocol.hy を読む・#2810)。

protocol.hy は Hy の module なので、pyright は中を読めず、coordinator の GET /metrics の答え PlainText を読む使い手(模擬の検が計器の
行を読む所など)の strict に、書き手に直せない Unknown の赤(Type of "PlainText" is unknown・Type of "text" is unknown)が出た。
readiness_model.pyi と同じ形で宣言する。

- ClusterTiming・Request・PlainText は凍った dataclass(Request は eq を持たない)。欄の名と順・既定値の有無は実装と同じ。
- NextRequests・Reply・CoordinatorStopRequested は凍った dataclass の EffectBase(答え = Request の list・None・bool)。
- BodyInvalid は ValueError の子(送り手の本文の誤り)。
- 実装との食い違いは packages/doeff-cluster/tests/test_meter_report_static_types.py が検める。
"""

from dataclasses import dataclass

from doeff import EffectBase

PROTOCOL_FORMAT: int
WATCH_MAX_SECONDS: float

@dataclass(frozen=True)
class ClusterTiming:
    lease_ms: int = 10000
    fence_ms: int = 20000
    reassign_after_ms: int = 45000
    silent_worker_wait_ms: int = ...
    keep_fence_ms: int = 240000

@dataclass(frozen=True, eq=False)
class Request:
    """受けた HTTP 要求 1 件。"""

    method: str
    path: str
    query: dict[str, object]
    body: object
    parts: tuple[str, ...]
    slot: object = None
    actor: str | None = None
    peer: str = ""

class BodyInvalid(ValueError): ...

@dataclass(frozen=True)
class PlainText:
    """JSON でない返事の本文(GET /metrics の Prometheus の text)。"""

    text: str
    content_type: str = ...

@dataclass(frozen=True)
class NextRequests(EffectBase[list[Request]]):
    """受付に並んだ要求をまとめて取る(答え = Request の list)。"""

    timeout_seconds: float
    limit: int = 256

@dataclass(frozen=True)
class Reply(EffectBase[None]):
    request: Request
    status: int
    body: object

@dataclass(frozen=True)
class CoordinatorStopRequested(EffectBase[bool]):
    """答えは bool。"""
