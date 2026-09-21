"""専用pingと通常sessionの分離を実sessionhostとCLI替え玉で検証する。"""
import json
import time
from pathlib import Path

from test_sessionhost_headless import Host, _launch_params, _wait_turn_end, _pause
from test_sessionhost_headless import headless_host as headless_host

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
    assert isinstance(receipt, dict) and receipt["state"] == "succeeded", receipt
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
    assert receipt["reply"]["cache_read"] == 2


def test_host_cache_ping_expiry_never_changes_the_normal_session(headless_host: Host) -> None:
    sid = "cache-expired"
    headless_host.ok("session.launch", _launch_params(headless_host.root, sid, "claude"))
    before = _wait_turn_end(headless_host, sid)
    result = headless_host.ok("session.cache-ping", {
        "session_id": sid, "operation_id": "expired", "expires_at": 1,
    })
    assert isinstance(result, dict) and result["state"] == "expired", result
    assert headless_host.snap(sid) == before

