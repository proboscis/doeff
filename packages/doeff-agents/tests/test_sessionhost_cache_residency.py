"""通常のidle回収と専用pingを同じsessionhostの永続状態で検証する。"""

import sqlite3
from dataclasses import replace
from pathlib import Path

from doeff_agents.sessionhost.acp.cache_operation import CacheReply, MaintenanceState
from doeff_agents.sessionhost.acp.effects import SessionView
from doeff_agents.sessionhost.acp.handlers import session_view_of
from doeff_agents.sessionhost.acp.judgment import sessions_to_retire
from doeff_agents.sessionhost.cache_host_model import HostCacheRecord
from doeff_agents.sessionhost.cache_host_store import cache_receipt_put, cache_retained_until
from test_sessionhost_headless import Host, _launch_params, _wait_turn_end
from test_sessionhost_headless import headless_host as headless_host

from doeff import run


def _view(host: Host, sid: str) -> SessionView:
    view = session_view_of(host.snap(sid))
    assert view is not None
    return view


def _save_receipt(host: Host, receipt: HostCacheRecord) -> None:
    def write(conn: object) -> None:
        assert isinstance(conn, sqlite3.Connection)
        cache_receipt_put(conn, receipt)
    host.actor.submit(write)


def test_idle_cleanup_keeps_cache_resident_until_first_55_minute_ping(headless_host: Host) -> None:
    sid = "cache-residency-initial"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    _wait_turn_end(headless_host, sid)
    view = _view(headless_host, sid)
    assert view.turn_ended_at_ms is not None
    for elapsed in (600_000, 3_300_000):
        assert run(sessions_to_retire((view,), view.turn_ended_at_ms + elapsed, 600)) == ()
    # 保持は有限。pingが一度も成功しなければ1時間後に回収できる。
    assert run(sessions_to_retire((view,), view.turn_ended_at_ms + 3_600_000, 600)) == (sid,)


def test_successful_ping_keeps_second_cycle_without_forging_turn_end(headless_host: Host) -> None:
    sid = "cache-residency-second"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    before = _wait_turn_end(headless_host, sid)
    original = _view(headless_host, sid)
    assert original.turn_ended_at_ms is not None
    start = original.turn_ended_at_ms
    reply = CacheReply("reply", start + 3_300_000, start + 3_302_000, "model", 3600, 64000, 42)
    receipt = HostCacheRecord("first-ping", sid, start + 3_600_000, "ping", "events",
                              MaintenanceState.SUCCEEDED, reply.started_at, reply)
    _save_receipt(headless_host, receipt)
    # DBを読み直すsession.getで観測する。手番の時計は最初のまま。
    after = _view(headless_host, sid)
    assert after.turn_ended_at_ms == start
    assert headless_host.snap(sid)["turn_ended_at"] == before["turn_ended_at"]
    assert run(sessions_to_retire((after,), start + 6_600_000, 600)) == ()
    assert run(sessions_to_retire((after,), reply.completed_at + 3_600_000, 600)) == (sid,)
    # 2回目が応答不明なら保持期限を延長しない。
    failed = replace(receipt, operation_id="second-ping", state=MaintenanceState.UNKNOWN,
                     started_at=start + 6_600_000, reply=None, reason="lost-response")
    _save_receipt(headless_host, failed)
    assert _view(headless_host, sid) == after
    # 明示的cancelは保持期限に優先する。
    headless_host.ok("session.cancel", {"session_id": sid})
    assert _view(headless_host, sid).status == "stopped"
    assert _view(headless_host, sid).cache_retained_until_ms is None


def test_cache_residency_does_not_extend_codex_idle_lifetime(headless_host: Host) -> None:
    sid = "cache-residency-codex"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "codex"))
    _wait_turn_end(headless_host, sid)
    view = _view(headless_host, sid)
    assert view.turn_ended_at_ms is not None
    assert run(sessions_to_retire((view,), view.turn_ended_at_ms + 600_000, 600)) == (sid,)


def test_cache_residency_reopens_existing_receipts_and_ignores_unknown(tmp_path: Path) -> None:
    db = tmp_path / "receipts.db"
    reply = CacheReply("response", 3_300_000, 3_302_000, "model", None, 64000, 0)
    record = HostCacheRecord("ping", "session", 3_600_000, "process", "events",
                             MaintenanceState.SUCCEEDED, reply.started_at, reply)
    with sqlite3.connect(db) as conn:
        cache_receipt_put(conn, record)
    with sqlite3.connect(db) as conn:
        assert cache_retained_until(conn, "session") == 6_902_000
        assert cache_retained_until(conn, "other-session") is None
        cache_receipt_put(conn, replace(record, operation_id="unknown",
                          state=MaintenanceState.UNKNOWN, reply=None))
        assert cache_retained_until(conn, "session") == 6_902_000
