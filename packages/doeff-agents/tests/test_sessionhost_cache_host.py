"""専用pingと通常sessionの分離を実sessionhostとCLI替え玉で検証する。"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from doeff_agents.sessionhost.acp.cache_operation import MaintenanceState
from doeff_agents.sessionhost.cache_host_model import HostCacheRecord
from doeff_agents.sessionhost.cache_host_store import cache_receipt_get, cache_receipt_put
from doeff_agents.sessionhost.cache_process import identify_process
from doeff_agents.sessionhost.impls.claude_code import CLAUDE_AUTO_MEMORY_DIR_SETTING
from doeff_agents.sessionhost.impls.headless_argv import build_claude_headless
from test_sessionhost_headless import Host, _launch_params, _pause, _wait_turn_end
from test_sessionhost_headless import headless_host as headless_host

from doeff import run


def test_host_cache_ping_is_a_separate_idempotent_operation(headless_host: Host) -> None:
    """通常sessionの状態・イベントを変えず、同じ要求の再送でもpingは一度だけ。"""
    sid = "cache-special"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    before = _wait_turn_end(headless_host, sid)
    params = {"session_id": sid, "operation_id": "ping-one",
              "expires_at": int(time.time() * 1000) + 60000}
    headless_host.ok("session.cache-ping", params)
    deadline = time.monotonic() + 10
    receipt = None
    while time.monotonic() < deadline:
        receipt = headless_host.ok("session.cache-ping", params)
        assert isinstance(receipt, dict)
        if receipt["state"] == "succeeded":
            break
        _pause(0.05)
    assert isinstance(receipt, dict)
    assert receipt["state"] == "succeeded", receipt
    assert headless_host.ok("session.cache-ping", params) == receipt
    after = headless_host.snap(sid)
    for field in ("awaiting_response", "turn_ended_at", "turn_holder", "turn_wait",
                  "result_payload", "backend_ref", "status", "last_observed_at"):
        assert before.get(field) == after.get(field), field
    events_path = receipt["events_path"]
    assert isinstance(events_path, str)
    records = [json.loads(line) for line in Path(events_path).read_text().splitlines()]
    replies = [row for row in records if row.get("type") == "assistant"]
    assert len(replies) == 1
    assert "this is a ping, only answer with ping" in str(replies[0])
    reply = receipt["reply"]
    assert isinstance(reply, dict)
    assert reply["cache_read"] == 2


def test_host_cache_ping_expiry_never_changes_the_normal_session(headless_host: Host) -> None:
    sid = "cache-expired"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    before = _wait_turn_end(headless_host, sid)
    result = headless_host.ok("session.cache-ping", {
        "session_id": sid, "operation_id": "expired", "expires_at": 1,
    })
    assert isinstance(result, dict)
    assert result["state"] == "expired", result
    assert headless_host.snap(sid) == before


def test_cache_receipt_survives_reopen_and_forbids_resend(tmp_path: Path) -> None:
    db = tmp_path / "cache.db"
    record = HostCacheRecord("operation", "session", 10000, "process", "events")
    with sqlite3.connect(db) as conn:
        cache_receipt_put(conn, record)
        running = replace(record, state=MaintenanceState.RUNNING, started_at=10)
        cache_receipt_put(conn, running)
    with sqlite3.connect(db) as conn:
        assert cache_receipt_get(conn, "operation") == running
        with pytest.raises(ValueError, match="未送信"):
            cache_receipt_put(conn, record)
        with pytest.raises(sqlite3.IntegrityError):
            cache_receipt_put(conn, replace(record, operation_id="duplicate"))
        conn.rollback()
        finished = replace(running, state=MaintenanceState.UNKNOWN, reason="lost-response")
        cache_receipt_put(conn, finished)
        with pytest.raises(ValueError, match="完了"):
            cache_receipt_put(conn, running)


def test_cache_ping_keeps_model_and_settings_without_running_work_hooks() -> None:
    params = {"session_hooks": "inherit", "claude_settings": {"hooks": {}},
              "memory_dir": "/same-memory", "model": "same-model", "effort": "high",
              "resume_mode": "resume", "conversation": {"session_id": "same-session"},
              "cache_maintenance": True}
    built = run(build_claude_headless(params))
    args = built["argv"]
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings == {CLAUDE_AUTO_MEMORY_DIR_SETTING: "/same-memory", "hooks": {}, "disableAllHooks": True}
    assert args[args.index("--resume") + 1] == "same-session"
    assert args[args.index("--model") + 1] == "same-model"
    assert args[args.index("--max-turns") + 1] == "1"


def test_cache_ping_and_normal_send_never_write_same_history_together(headless_host: Host) -> None:
    sid = "cache-exclusive"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    _wait_turn_end(headless_host, sid)
    params = {"session_id": sid, "operation_id": "ping-exclusive",
              "expires_at": int(time.time() * 1000) + 60000,
              "session_env": {"DOEFF_HEADLESS_STUB_DELAY": "3"}}
    first = headless_host.ok("session.cache-ping", params)
    assert isinstance(first, dict)
    assert first["state"] == "running"
    response = headless_host.call("session.send", {"session_id": sid, "message": "work", "awaiting": True})
    assert response["ok"] is False
    assert "cache-maintenance-active" in str(response["error"])
    headless_host.ok("session.cancel", {"session_id": sid})
    cancelled = headless_host.ok("session.cache-ping-status", {
        "session_id": sid, "operation_id": "ping-exclusive",
    })
    assert isinstance(cancelled, dict)
    assert cancelled["state"] == "failed"
    assert cancelled["reason"] == "session-cancelled"


def test_dead_host_ping_is_stopped_before_recovered_host_releases_session(headless_host: Host) -> None:
    """実際に親processを失わせる。復旧側には元のregistryもstdioも無い。"""
    child_source = """
import json, os, sys, time
from pathlib import Path
from test_sessionhost_headless import Host, _launch_params, _wait_turn_end
h = Host(Path(sys.argv[1]))
sid = "cache-host-death"
h.ok("session.launch", _launch_params(h.root, sid, "claude"))
_wait_turn_end(h, sid)
params = {"session_id": sid, "operation_id": "crashed-host-ping",
          "expires_at": int(time.time() * 1000) + 3000,
          "session_env": {"DOEFF_HEADLESS_STUB_DELAY": "30"}}
until = time.monotonic() + 2
while time.monotonic() < until:
    receipt = h.ok("session.cache-ping", params)
    if receipt.get("process"):
        print(json.dumps(receipt), flush=True)
        os._exit(0)
    time.sleep(0.01)
raise RuntimeError("ping was not started")
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    result = subprocess.run(
        [sys.executable, "-c", child_source, str(headless_host.root)],
        env=env, capture_output=True, text=True, timeout=10, check=True,
    )
    receipt = json.loads(result.stdout.splitlines()[-1])
    pid = receipt["process"]["pid"]
    assert identify_process(pid) is not None, "孤児processが実際に残る条件で検証する"
    remaining = max(0, receipt["expires_at"] / 1000 - time.time())
    _pause(remaining + 0.05)
    recovered = headless_host.ok("session.cache-ping-status", {
        "session_id": "cache-host-death", "operation_id": "crashed-host-ping",
    })
    assert isinstance(recovered, dict)
    assert recovered["state"] == "unknown", recovered
    assert identify_process(pid) is None
    # 同じDBを読む新しいhostが、専用操作の死亡確認後に通常入力を受け付ける。
    headless_host.ok("session.send", {
        "session_id": "cache-host-death", "message": "normal work", "awaiting": True,
    })
