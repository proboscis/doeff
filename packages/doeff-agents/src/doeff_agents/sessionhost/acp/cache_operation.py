"""通常のagent-jobから独立した、キャッシュ維持操作の型付き契約。

枠・優先度・任意promptは入力に持たない。実I/Oはhandlerが所有する。
送信結果不明は再観測し、通常ターンへのfallbackや盲目的な再送をしない。
"""

from dataclasses import dataclass
from enum import StrEnum

from doeff import EffectBase

PING_TEXT = "this is a ping, only answer with ping"

#: 温かい session を専用操作の送信先として保つ予算(ms)。provider の cache の有効期限を証明する値では
#: なく、通常の idle 回収から送信先を守るための上限ちょうど(対応する cache TTL の最大 1 時間)。
#: card acp:kanban-issue:ki-567f2dd6140f §3.1e: 「いつまで保持するか」は**判断**なので ACP 側が持つ
#: (judgment.cache-resident-retention-of の 1 点が読む)。host はこの値を 1 度も見ない。
CACHE_RESIDENT_IDLE_MS = 3_600_000


@dataclass(frozen=True)
class CacheTarget:
    conversation: str
    session: str
    node: str
    profile: str
    account: str
    model: str
    generation: int


@dataclass(frozen=True)
class CacheOperation:
    key: str
    target: CacheTarget
    observed_response: str
    expires_at: int


class MaintenanceState(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CacheReply:
    response_id: str
    started_at: int
    completed_at: int
    model: str
    ttl_seconds: int | None
    cache_read: int
    cache_write: int

    def __post_init__(self) -> None:
        if not self.response_id or self.completed_at < self.started_at:
            raise ValueError("キャッシュ応答の識別子・時刻が不正です")
        if min(self.started_at, self.cache_read, self.cache_write) < 0:
            raise ValueError("時刻・トークン数は非負です")
        if self.ttl_seconds not in (None, 300, 3600):
            raise ValueError("未知のキャッシュTTLです")


@dataclass(frozen=True)
class MaintenanceRecord:
    operation: CacheOperation
    revision: int
    state: MaintenanceState = MaintenanceState.REQUESTED
    started_at: int | None = None
    finished_at: int | None = None
    reply: CacheReply | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ResidentCache:
    target: CacheTarget
    available: bool
    boundary_allowed: bool


@dataclass(frozen=True)
class PingRunning:
    """専用操作をsessionhostが所有している。通常ターンの開始ではない。"""


@dataclass(frozen=True)
class PingCompleted:
    reply: CacheReply


@dataclass(frozen=True)
class PingFailed:
    reason: str


@dataclass(frozen=True)
class PingMissing:
    """実行証拠が見えない。送信されなかったという証拠にはしない。"""


PingOutcome = PingRunning | PingCompleted | PingFailed | PingMissing


class MaintenanceTransportUncertainError(ConnectionError):
    """送信の成否を確定できない。次回は同じ操作を照会する。"""


@dataclass(frozen=True)
class MaintenanceNow(EffectBase):
    pass


@dataclass(frozen=True)
class ReadMaintenance(EffectBase):
    key: str


@dataclass(frozen=True)
class InspectResidentCache(EffectBase):
    target: CacheTarget


@dataclass(frozen=True)
class ClaimMaintenance(EffectBase):
    """revisionをCASしrequestedからrunningへ。勝者だけが送信する。"""

    record: MaintenanceRecord
    now: int


@dataclass(frozen=True)
class StartMaintenancePing(EffectBase):
    """session.cache-ping専用RPC。通常のSessionSendには翻訳しない。"""

    operation: CacheOperation


@dataclass(frozen=True)
class ProbeMaintenancePing(EffectBase):
    operation: CacheOperation


@dataclass(frozen=True)
class FinishMaintenance(EffectBase):
    """専用操作の結果だけをCAS保存する。会話・仕事・turn-recordは変更しない。"""

    previous: MaintenanceRecord
    updated: MaintenanceRecord


@dataclass(frozen=True)
class AcpCacheOperations(EffectBase):
    """対象nodeの生きた専用操作だけをindexで読む。"""

    node_row: str


@dataclass(frozen=True)
class SessionCachePing(EffectBase):
    operation: CacheOperation
    session_env: dict[str, str]


@dataclass(frozen=True)
class SessionCacheProbe(EffectBase):
    operation: CacheOperation


@dataclass(frozen=True)
class BorrowCacheCredential(EffectBase):
    operation: CacheOperation


@dataclass(frozen=True)
class ReleaseCacheCredential(EffectBase):
    operation: CacheOperation
