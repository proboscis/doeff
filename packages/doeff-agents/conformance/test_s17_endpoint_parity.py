"""S17: in-process result endpoint ↔ host endpoint parity(README 行列 S17、
C1 で器を凍結 → C3 で本体着地).

契約(ハザード 5): result チャネルの意味論が直接束縛(per-kind ゲートが使う
in-process endpoint)と転送束縛(daemon wire + report-result-mcp relay)で
一致しなければならない。検査面は 3 つ:

1. **wire 面**(daemon 制御 socket の `session.report_result` /
   `session.await_result` 応答): -32002 schema reject / -32003
   already-terminal / `already_reported:true` idempotency はここに現れる。
2. **agent 可視面**(report-result-mcp relay の MCP tools/call 応答):
   `{"content":[{"text":...}],"isError":bool}` + schema 文言のみ。数値
   エラーコードは現れない(S4 で凍結済みの観測 — relay が透過し始めたら
   parity break)。
3. **byte-faithful**: await_result が返す payload と行の
   `result_payload_json` は報告 JSON と byte 一致(S1 / ADR 0035)。

in-process 側 = Hy session host の dispatch(`dispatch_line`、daemon /
socket / tmux 抜きの直接束縛 — deftest が使うのと同じ束縛)。wire 側 =
このゲートの daemon(oracle ゲートでは Rust、`CONFORMANCE_AGENTD_BIN`
ゲートでは Hy host)。同一 session_id・同一 payload で同じ副シナリオ
(schema-invalid 報告 / valid 報告 / 再報告 / 終端後報告)を両束縛で駆動し、
report 系は **封筒全体の等値**(id 除去)で突き合わせる — oracle ゲートでは
これが「Hy 直接束縛 ≡ Rust wire」の最強アンカーになる。

suite 依存の注記: in-process 半分だけが doeff_agents(Hy host)を import
する。これは S17 の契約そのもの(直接束縛を駆動しないと parity は検査
できない)であり、wire client + stdlib のみで書く他の S 行の規律とは
意図的に異なる(器の docstring が C1 時点から予告していたスコープ)。
"""

import json
import socket as socket_mod
import time
from pathlib import Path

from harness import RESULT_SCHEMA, AgentdHarness

PROMPT = "Produce the conformance structured result."
PAYLOAD = {"summary": "endpoint parity", "ok": True}
INVALID_PAYLOAD = {"summary": "missing required field"}
PROCEED_MARKER = "S17-PROCEED"
# serde_json::to_string parity: compact, non-ASCII passthrough, and SORTED
# keys (serde's Value map is a BTreeMap — the daemon canonicalizes key order,
# it does not preserve the reporter's)
COMPACT_PAYLOAD_JSON = json.dumps(
    PAYLOAD, sort_keys=True, separators=(",", ":"), ensure_ascii=False
)


def _wire_rpc(socket_path: Path, method: str, params: dict) -> dict:
    """Raw JSON-lines RPC against the daemon control socket; returns the
    decoded envelope with the request id stripped (parity compares
    everything else)."""
    with socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM) as sock:
        sock.settimeout(10.0)
        sock.connect(str(socket_path))
        sock.sendall(
            (json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode(
                "utf-8"
            )
        )
        with sock.makefile("r", encoding="utf-8") as reader:
            line = reader.readline()
    envelope = json.loads(line)
    envelope.pop("id")
    return envelope


def _inproc_dispatch(hyhost, config, actor, method: str, params: dict) -> dict:
    """The same request through the in-process binding (host dispatch
    without daemon/socket); returns the envelope with id stripped."""
    line = hyhost.dispatch_line(
        json.dumps({"id": 1, "method": method, "params": params}), config, actor
    )
    envelope = json.loads(line)
    envelope.pop("id")
    return envelope


def _snapshot(session_id: str, *, status: str, expected_result: dict | None) -> dict:
    """Minimal full-shape snapshot for seeding the in-process store (the
    direct binding has no launch/tmux; rows enter via the store, exactly
    like the sessionhost deftests do)."""
    return {
        "session_id": session_id,
        "session_name": session_id,
        "pane_id": "%0",
        "agent_type": "claude",
        "work_dir": "/tmp",
        "lifecycle": "run_to_completion",
        "status": status,
        "backend_kind": "tmux",
        "backend_ref": {},
        "started_at": "2026-01-01T00:00:00+00:00",
        "last_observed_at": None,
        "finished_at": None,
        "cleaned_at": None,
        "pr_url": None,
        "output_snippet": None,
        "terminal_cause": None,
        "expected_result": expected_result,
        "retries_used": 0,
        "last_validation_error": None,
        "awaiting_response": False,
        "observed_active_at": None,
        "result_payload": None,
        "result_solicitations_used": 0,
        "prompt_unblock_attempts": 0,
        "last_output_change_at": None,
        "effective_identity": None,
    }


def test_s17_inproc_and_host_result_endpoints_agree(tmp_path) -> None:
    # ------------------------------------------------------------------
    # (b) host endpoint: live daemon + relay, wire + agent surfaces
    # ------------------------------------------------------------------
    with AgentdHarness() as harness:
        scenario = harness.scenario(
            "s17",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
                # keep the pane visibly ACTIVE so the monitor's turn-end /
                # solicitation machinery never interleaves with the report
                # sub-scenarios this test sequences explicitly
                {"render": "F-active-claude"},
                {"report_result": "schema_invalid"},
                {"await_keys": {"expect": PROCEED_MARKER, "timeout_s": 30}},
                {"report_result": {"payload": PAYLOAD}},
            ],
        )
        scenario.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )

        # deterministic sync: the fixture's rejected report has landed and
        # the fixture is parked on the PROCEED marker
        deadline = time.monotonic() + 30.0
        reports: list[dict] = []
        while time.monotonic() < deadline and not reports:
            reports = [
                e for e in scenario.journal() if e["event"] == "report_result"
            ]
            time.sleep(0.2)
        assert reports, f"fixture's rejected report never landed\n{harness.log_text()}"

        # wire, pre-terminal + no payload: schema rejection is -32002
        wire_invalid = _wire_rpc(
            harness.socket_path,
            "session.report_result",
            {"session_id": scenario.session_id, "payload": INVALID_PAYLOAD},
        )
        assert wire_invalid["ok"] is False, wire_invalid
        assert wire_invalid["error_code"] == -32002, wire_invalid
        assert wire_invalid["error"].startswith(
            "reported result does not satisfy its schema: "
        ), wire_invalid

        # unblock the fixture: valid report → result-first finalizes done.
        # enter=False + embedded newline: a submit-Enter would arm the
        # send's confirm-resubmit loop (oracle tmux_send_keys :2578-2613),
        # whose capture then races the cleanup that follows `done` — the
        # newline byte alone flushes the fixture's canonical-mode stdin.
        harness.client.send_session(
            scenario.session_id, PROCEED_MARKER + "\n", enter=False
        )
        outcome = harness.client.await_result(scenario.session_id, timeout_seconds=25.0)
        assert outcome.result == PAYLOAD and outcome.validation_error is None

        # wire, post-terminal WITH payload: idempotent already_reported —
        # for the repeated valid payload AND for an invalid one (terminal
        # short-circuits before validation; ADR 0035 R4 never re-validates)
        wire_again = _wire_rpc(
            harness.socket_path,
            "session.report_result",
            {"session_id": scenario.session_id, "payload": PAYLOAD},
        )
        assert wire_again == {
            "ok": True,
            "result": {"accepted": True, "already_reported": True},
        }, wire_again
        wire_invalid_after = _wire_rpc(
            harness.socket_path,
            "session.report_result",
            {"session_id": scenario.session_id, "payload": INVALID_PAYLOAD},
        )
        assert wire_invalid_after == wire_again, wire_invalid_after

        # wire await on the finalized session: payload comes back, and
        # byte-faithfully in the row
        wire_await = _wire_rpc(
            harness.socket_path,
            "session.await_result",
            {"session_id": scenario.session_id, "timeout_seconds": 5},
        )
        assert wire_await["ok"] is True
        assert wire_await["result"]["result"] == {"payload": PAYLOAD}, wire_await
        assert "validation_error" not in wire_await["result"], wire_await
        wire_row_payload = harness.session_row(scenario.session_id)[
            "result_payload_json"
        ]
        assert wire_row_payload == COMPACT_PAYLOAD_JSON

        # agent surface (relay): tool-level shape only, schema wording
        # verbatim from the wire error, and NO numeric wire codes leak
        reports = [e for e in scenario.journal() if e["event"] == "report_result"]
        assert len(reports) == 2, reports
        rejected = json.loads(reports[0]["response"])["result"]
        assert rejected["isError"] is True, reports[0]
        assert rejected["content"][0]["text"] == "Error: " + wire_invalid["error"], (
            reports[0]
        )
        assert "-32002" not in reports[0]["response"]
        assert "-32003" not in reports[0]["response"]
        recorded = json.loads(reports[1]["response"])["result"]
        assert recorded["isError"] is False, reports[1]
        assert recorded["content"][0]["text"] == "result recorded", reports[1]

        # wire, terminal WITHOUT payload: -32003 (second session, cancelled
        # before any report)
        scenario_b = harness.scenario(
            "s17b",
            [
                {"render": "F-idle-claude"},
                {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
            ],
        )
        scenario_b.launch_m2(
            prompt=PROMPT,
            expected_result={"payload_schema": RESULT_SCHEMA},
        )
        harness.client.cancel_session(scenario_b.session_id)
        row_b_status = harness.session_row(scenario_b.session_id)["status"]
        wire_terminal = _wire_rpc(
            harness.socket_path,
            "session.report_result",
            {"session_id": scenario_b.session_id, "payload": PAYLOAD},
        )
        assert wire_terminal["ok"] is False, wire_terminal
        assert wire_terminal["error_code"] == -32003, wire_terminal

        session_a = scenario.session_id
        session_b = scenario_b.session_id

    # ------------------------------------------------------------------
    # (a) in-process endpoint: the direct binding (host dispatch, no
    # daemon/socket/tmux), same session ids, same payloads, same order
    # ------------------------------------------------------------------
    import hy  # noqa: F401  # registers the .hy importer

    from doeff_agents.sessionhost import host as hyhost
    from doeff_agents.sessionhost import store as hystore

    db_path = str(tmp_path / "s17-inproc.sqlite")
    config = hyhost.parse_args(
        [
            "--db",
            db_path,
            "--socket",
            str(tmp_path / "s17-inproc.sock"),
            "--prompt-judge-cmd",
            "",
            "serve",
        ]
    )
    actor = hystore.StoreActor(db_path)
    try:
        contract = {"payload_schema": RESULT_SCHEMA}
        actor.submit(
            lambda conn: hystore.db_upsert_snapshot(
                conn,
                _snapshot(session_a, status="running", expected_result=contract),
            )
        )

        # pre-terminal schema rejection: envelope-identical to the wire
        inproc_invalid = _inproc_dispatch(
            hyhost,
            config,
            actor,
            "session.report_result",
            {"session_id": session_a, "payload": INVALID_PAYLOAD},
        )
        assert inproc_invalid == wire_invalid, (inproc_invalid, wire_invalid)

        # first valid report: accepted
        inproc_ok = _inproc_dispatch(
            hyhost,
            config,
            actor,
            "session.report_result",
            {"session_id": session_a, "payload": PAYLOAD},
        )
        assert inproc_ok == {"ok": True, "result": {"accepted": True}}, inproc_ok

        # finalize (status flip is the monitor's job on the wire; here the
        # direct binding re-upserts — COALESCE must keep the payload)
        actor.submit(
            lambda conn: hystore.db_upsert_snapshot(
                conn,
                _snapshot(session_a, status="done", expected_result=contract),
            )
        )

        # post-terminal: idempotent already_reported, valid and invalid
        # alike — envelope-identical to the wire observations
        inproc_again = _inproc_dispatch(
            hyhost,
            config,
            actor,
            "session.report_result",
            {"session_id": session_a, "payload": PAYLOAD},
        )
        assert inproc_again == wire_again, (inproc_again, wire_again)
        inproc_invalid_after = _inproc_dispatch(
            hyhost,
            config,
            actor,
            "session.report_result",
            {"session_id": session_a, "payload": INVALID_PAYLOAD},
        )
        assert inproc_invalid_after == wire_invalid_after, (
            inproc_invalid_after,
            wire_invalid_after,
        )

        # await parity: same payload back, no validation_error
        inproc_await = _inproc_dispatch(
            hyhost,
            config,
            actor,
            "session.await_result",
            {"session_id": session_a, "timeout_seconds": 5},
        )
        assert inproc_await["ok"] is True
        assert inproc_await["result"]["result"] == wire_await["result"]["result"], (
            inproc_await,
            wire_await,
        )
        assert "validation_error" not in inproc_await["result"], inproc_await

        # byte-faithful across bindings: identical raw column text
        inproc_row_payload = actor.submit(
            lambda conn: hystore.db_current_result_payload(conn, session_a)
        )
        assert inproc_row_payload == wire_row_payload == COMPACT_PAYLOAD_JSON

        # terminal-without-payload parity: same status the wire cancel
        # produced, envelope-identical -32003
        actor.submit(
            lambda conn: hystore.db_upsert_snapshot(
                conn,
                _snapshot(session_b, status=row_b_status, expected_result=contract),
            )
        )
        inproc_terminal = _inproc_dispatch(
            hyhost,
            config,
            actor,
            "session.report_result",
            {"session_id": session_b, "payload": PAYLOAD},
        )
        assert inproc_terminal == wire_terminal, (inproc_terminal, wire_terminal)
    finally:
        actor.close()
