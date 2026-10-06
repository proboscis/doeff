"""shared/intent/semaphore_model.hy の公開面の型(型検査のための宣言 — 実行時は semaphore_model.hy を読む)。

semaphore_model.hy は Hy の module なので、pyright は中を読めず、名が全部 Unknown になる。LeaseOp の答えを型の値 LeaseAnswer に
した時(#2523)、使い手の repo の模擬の世界が答えを LeaseAnswer で受けると、書き手に直せない赤(Type of "LeaseAnswer" is unknown)が
出た。ここで型を宣言する(launch.pyi・request_bodies.pyi と同じ形)。

- effect(HeldLease・LeaseOp・LeaseStanding)は凍った dataclass の EffectBase[答えの型]。
- LeaseAnswer は defwire の型(欄の名は Python の綴り — wire の名は camel)。
- CreateNamedSemaphore・ClusterSemaphore は scheduler の CreateSemaphore・Semaphore の子(欄 name を足す)。
"""

from dataclasses import dataclass

from doeff import EffectBase
from doeff_core_effects.scheduler import CreateSemaphore, Semaphore

MODULE_TAGS: dict[str, str]
SEMAPHORE_PREFIX: str
FENCE_MARGIN_MS: int
LEASE_OPS: tuple[str, ...]
LEASE_MAX_TTL_MS: int
STANDBY: str
HELD: str
LOST: str

class CreateNamedSemaphore(CreateSemaphore):
    name: str
    def __init__(self, name: str, permits: int = ...) -> None: ...

class ClusterSemaphore(Semaphore):
    name: str
    permits: int
    def __init__(self, name: str, permits: int) -> None: ...

class LeaseLost(RuntimeError): ...
class WriteFenced(RuntimeError): ...

@dataclass(frozen=True)
class HeldLease(EffectBase[dict[str, object] | None]):
    """答え = 名前の最も古い token とその確かめた期限(持っていない・失った = None — semaphore_handlers の hold-of)。"""

    name: str

@dataclass(frozen=True, kw_only=True)
class LeaseAnswer:
    ok: bool
    reason: str | None
    ttl_ms: int
    dropped: int

@dataclass(frozen=True)
class LeaseOp(EffectBase[LeaseAnswer]):
    name: str
    op: str
    token: str
    permits: int = ...
    ttl_ms: int = ...

@dataclass(frozen=True)
class AwaitLeaseFree(EffectBase[bool]):
    name: str

@dataclass(frozen=True)
class LeaseStanding(EffectBase[str]):
    name: str
