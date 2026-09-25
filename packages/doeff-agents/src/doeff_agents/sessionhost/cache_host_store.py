"""専用操作のローカルreceipt。sessionhostのSQLite actorからだけ呼ぶI/O境界。"""

import json
import sqlite3
from _thread import RLock
from dataclasses import asdict
from weakref import WeakValueDictionary

from doeff_agents.sessionhost.acp.cache_operation import MaintenanceState
from doeff_agents.sessionhost.cache_host_model import (
    HostCacheRecord,
    decode_cache_receipt,
)

_locks: WeakValueDictionary[str, RLock] = WeakValueDictionary()
_locks_guard = RLock()


def session_mutation_lock(session_id: str) -> RLock:
    """通常sendと専用操作の開始を同じ短い排他区間に入れる。LLMの完了は待たない。"""
    with _locks_guard:
        lock = _locks.get(session_id)
        if lock is None:
            lock = RLock()
            _locks[session_id] = lock
        return lock


def _table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache_maintenance ("
        "operation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "state TEXT NOT NULL, receipt_json TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS cache_maintenance_active_session "
        "ON cache_maintenance(session_id) WHERE state IN ('requested','running')"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS cache_maintenance_session_state "
        "ON cache_maintenance(session_id,state)"
    )


def cache_last_success_at(conn: sqlite3.Connection, session_id: str) -> int | None:
    """この session で最後に成功した専用操作の完了時刻(epoch ms・無ければ None)。

    会話全体の履歴やACPを走査せず、indexで対象sessionの成功操作だけを読む。
    これは**観測した事実**ちょうどで、cacheの実在・送信資格・保持の期限を1つも宣言しない
    (card acp:kanban-issue:ki-567f2dd6140f §3.1e —— 期限の判断は ACP 側の
    judgment.cache-resident-retention-of の1点が、この値と turn_ended_at から導く)。
    """
    _table(conn)
    row = conn.execute(
        "SELECT MAX(json_extract(receipt_json, '$.reply.completed_at')) "
        "FROM cache_maintenance WHERE session_id = ? AND state = 'succeeded' "
        "AND (json_extract(receipt_json, '$.reply.cache_read') > 0 "
        "OR json_extract(receipt_json, '$.reply.cache_write') > 0)",
        (session_id,),
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def cache_receipt_get(conn: sqlite3.Connection, operation_id: str) -> HostCacheRecord | None:
    _table(conn)
    row = conn.execute(
        "SELECT receipt_json FROM cache_maintenance WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    return None if row is None else decode_cache_receipt(json.loads(row[0]))


def cache_receipt_active(conn: sqlite3.Connection, session_id: str) -> HostCacheRecord | None:
    _table(conn)
    row = conn.execute(
        "SELECT receipt_json FROM cache_maintenance WHERE session_id = ? "
        "AND state IN ('requested','running')", (session_id,)
    ).fetchone()
    return None if row is None else decode_cache_receipt(json.loads(row[0]))


def cache_receipt_put(conn: sqlite3.Connection, record: HostCacheRecord) -> None:
    """専用操作の記録を 1 件書く(未送信へ戻す・送信先を変える・完了後に変える書きは断る)。

    確定は呼び手の connection に任せ、ここでは commit しない。store の actor の connection は
    autocommit で、明示の transaction の中で呼ばれた時に閉じてよいのは db-immediate-transaction
    だけ(ADR-DOE-AGENTS-004 R15・test_sessionhost_transaction_owner.py)。
    """
    _table(conn)
    existing = cache_receipt_get(conn, record.operation_id)
    if existing is not None and (
        existing.session_id != record.session_id or existing.expires_at != record.expires_at
        or existing.process_name != record.process_name or existing.events_path != record.events_path
    ):
        raise ValueError("専用操作IDが別の要求に使われています")
    if (existing is not None and existing.state == MaintenanceState.RUNNING
            and record.state == MaintenanceState.REQUESTED):
        raise ValueError("送信の記録を未送信へ戻すことはできません")
    if existing is not None and existing.process is not None and existing.process != record.process:
        raise ValueError("送信先processの識別を変更することはできません")
    if existing is not None and existing.state not in (
        MaintenanceState.REQUESTED, MaintenanceState.RUNNING
    ):
        if existing != record:
            raise ValueError("完了した専用操作の記録を変更できません")
        return
    conn.execute(
        "INSERT INTO cache_maintenance(operation_id,session_id,state,receipt_json) "
        "VALUES(?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET "
        "state=excluded.state,receipt_json=excluded.receipt_json",
        (record.operation_id, record.session_id, record.state, json.dumps(asdict(record))),
    )
