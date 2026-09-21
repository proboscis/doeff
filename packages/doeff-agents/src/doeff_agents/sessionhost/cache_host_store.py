"""専用操作のローカルreceipt。sessionhostのSQLite actorからだけ呼ぶI/O境界。"""

import json
import sqlite3
from _thread import RLock
from dataclasses import asdict
from weakref import WeakValueDictionary

from doeff_agents.sessionhost.acp.cache_operation import MaintenanceState
from doeff_agents.sessionhost.cache_host_model import HostCacheRecord, decode_cache_receipt

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
    conn.commit()
