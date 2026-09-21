"""専用操作のACP境界。秘密を扱わず、wireの欠落を成功に丸めない。"""

from doeff_agents.sessionhost.acp.cache_operation import (
    CacheOperation,
    CacheReply,
    CacheTarget,
    MaintenanceRecord,
    MaintenanceState,
)
from doeff_agents.sessionhost.acp.effects import AcpRow, JSONObject


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("専用操作の文字列が欠けています")
    return value


def _int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("専用操作の時刻・世代が不正です")
    return value


def maintenance_of_row(row: AcpRow) -> MaintenanceRecord:
    if row.kind != "cache-operation":
        raise ValueError("専用操作以外を受け取りました")
    spec, status = row.spec, row.status or {}
    target = CacheTarget(
        _text(spec.get("conversationId")), _text(spec.get("sessionId")),
        _text(spec.get("nodeRow")), _text(spec.get("profile")),
        _text(spec.get("account")), _text(spec.get("model")),
        _int(spec.get("declarationGeneration")),
    )
    operation = CacheOperation(row.key, target, _text(spec.get("observedResponseId")),
                               _int(spec.get("expiresAt")))
    state = MaintenanceState(_text(status.get("state")))
    started, finished = status.get("startedAt"), status.get("finishedAt")
    raw_reply, reply = status.get("cacheObservation"), None
    if isinstance(raw_reply, dict):
        ttl = raw_reply.get("ttlSeconds")
        reply = CacheReply(
            _text(raw_reply.get("responseId")), _int(raw_reply.get("requestStartedAtLowerBound")),
            _int(raw_reply.get("at")), _text(raw_reply.get("model")),
            None if ttl is None else _int(ttl), _int(raw_reply.get("cacheRead")),
            _int(raw_reply.get("cacheWrite")),
        )
    reason = status.get("reason")
    if state == MaintenanceState.SUCCEEDED and reply is None:
        raise ValueError("成功した専用操作の観測がありません")
    return MaintenanceRecord(operation, row.generation, state,
                             None if started is None else _int(started),
                             None if finished is None else _int(finished), reply,
                             None if reason is None else _text(reason))


def maintenance_status(record: MaintenanceRecord) -> JSONObject:
    status: JSONObject = {"state": record.state.value}
    if record.started_at is not None:
        status["startedAt"] = record.started_at
    if record.finished_at is not None:
        status["finishedAt"] = record.finished_at
    if record.reason is not None:
        status["reason"] = record.reason
    if record.reply is not None:
        reply = record.reply
        status["cacheObservation"] = {
            "responseId": reply.response_id, "at": reply.completed_at,
            "requestStartedAtLowerBound": reply.started_at, "model": reply.model,
            "ttlSeconds": reply.ttl_seconds, "cacheRead": reply.cache_read,
            "cacheWrite": reply.cache_write,
        }
    return status
