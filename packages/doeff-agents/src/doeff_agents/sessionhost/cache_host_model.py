"""sessionhostの専用cache-ping。通常session行の完了状態とは別に記録する。"""

from dataclasses import dataclass

from doeff import EffectBase
from doeff_agents.sessionhost.acp.cache_operation import CacheReply, MaintenanceState

CACHE_MAINTENANCE_ACTIVE = "cache-maintenance-active"


class CacheMaintenanceActiveError(RuntimeError):
    """専用操作が同じ会話を使用中。通常入力は未送信なので後で再試行できる。"""


@dataclass(frozen=True)
class HostCacheRecord:
    operation_id: str
    session_id: str
    expires_at: int
    process_name: str
    events_path: str
    state: MaintenanceState = MaintenanceState.REQUESTED
    started_at: int | None = None
    reply: CacheReply | None = None
    reason: str | None = None


@dataclass(frozen=True)
class HostCacheRead(EffectBase):
    operation_id: str


@dataclass(frozen=True)
class HostCacheActive(EffectBase):
    session_id: str


@dataclass(frozen=True)
class HostCacheWrite(EffectBase):
    record: HostCacheRecord

def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("専用操作の文字列が欠けています")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("専用操作の時刻・使用量が不正です")
    return value


def decode_cache_receipt(value: object) -> HostCacheRecord:
    if not isinstance(value, dict):
        raise ValueError("専用操作の記録がobjectではありません")
    reply = None
    raw_reply = value.get("reply")
    if raw_reply is not None:
        if not isinstance(raw_reply, dict):
            raise ValueError("専用操作の応答がobjectではありません")
        ttl = raw_reply.get("ttl_seconds")
        reply = CacheReply(
            _text(raw_reply.get("response_id")),
            _integer(raw_reply.get("started_at")),
            _integer(raw_reply.get("completed_at")),
            _text(raw_reply.get("model")),
            None if ttl is None else _integer(ttl),
            _integer(raw_reply.get("cache_read")),
            _integer(raw_reply.get("cache_write")),
        )
    started = value.get("started_at")
    reason = value.get("reason")
    return HostCacheRecord(
        _text(value.get("operation_id")), _text(value.get("session_id")),
        _integer(value.get("expires_at")), _text(value.get("process_name")),
        _text(value.get("events_path")), MaintenanceState(_text(value.get("state"))),
        None if started is None else _integer(started), reply,
        None if reason is None else _text(reason),
    )
