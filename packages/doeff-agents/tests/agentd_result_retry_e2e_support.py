from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from doeff_agents.agentd_client import AgentdClient
from doeff_agents.effects import AgentSessionLifecycle

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "ok"],
    "properties": {
        "summary": {"type": "string"},
        "ok": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def run_agentd_deterministic_failure_no_retry_e2e(tmp_path: Path) -> dict[str, Any]:
    """Run a real agentd + tmux contract where the agent reports a
    schema-invalid result over the report_result channel and then ends its
    turn without a valid result.

    ADR 0035 R4 / hard rule 7: this deterministic validation failure is NOT
    re-prompted. The session fails on first occurrence with zero retries.
    """
    _require_binary("cargo")
    _require_binary("tmux")

    packages_dir = Path(__file__).resolve().parents[2]
    agentd_crate = packages_dir / "doeff-agentd"
    _build_agentd(agentd_crate)

    runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-e2e-", dir="/tmp"))
    session_id = f"agentd-e2e-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    script_path = runtime_dir / "fake_interactive_agent.py"
    _write_fake_agent(script_path)

    db_path = runtime_dir / "agentd.sqlite"
    socket_path = runtime_dir / "agentd.sock"
    agentd_log_path = runtime_dir / "agentd.log"
    fake_log_path = work_dir / "fake-agent-events.jsonl"
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"

    agentd_bin = agentd_crate / "target" / "debug" / "doeff-agentd"
    agentd_proc: subprocess.Popen[str] | None = None
    try:
        with agentd_log_path.open("w", encoding="utf-8") as agentd_log:
            agentd_proc = subprocess.Popen(
                [
                    str(agentd_bin),
                    "--db",
                    str(db_path),
                    "--socket",
                    str(socket_path),
                    "--monitor-interval-ms",
                    "100",
                    "--max-running",
                    "2",
                    "serve",
                ],
                cwd=agentd_crate,
                stdout=agentd_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            client = AgentdClient(socket_path, timeout=2.0)
            _wait_for_agentd(client, agentd_proc, agentd_log_path)

            client.launch_session(
                session_id=session_id,
                session_name=session_id,
                agent_type="claude",
                work_dir=work_dir,
                command=command,
                prompt="Produce the e2e structured result.",
                lifecycle=AgentSessionLifecycle.RUN_TO_COMPLETION,
                session_env={
                    "DOEFF_RESULT_SESSION_ID": session_id,
                    "DOEFF_AGENTD_SOCKET": str(socket_path),
                    "DOEFF_AGENTD_BIN": str(agentd_bin),
                },
                expected_result={"payload_schema": RESULT_SCHEMA},
            )
            outcome = client.await_result(session_id, timeout_seconds=25.0)

        db_snapshot = _read_session_db_state(db_path, session_id)
        fake_events = _read_jsonl(fake_log_path)
        return {
            "await_result": outcome.result,
            "session_status": db_snapshot["status"],
            "retries_used": db_snapshot["retries_used"],
            "retry_events": db_snapshot["retry_events"],
            "rejected_events": db_snapshot["rejected_events"],
            "reported_invalid": any(
                event.get("event") == "reported-invalid" for event in fake_events
            ),
            "rejection_error": next(
                (
                    str(event.get("error", ""))
                    for event in fake_events
                    if event.get("event") == "reported-invalid"
                ),
                "",
            ),
            "result_payload_json": db_snapshot["result_payload_json"],
        }
    finally:
        _cleanup_tmux_session(session_id)
        if agentd_proc is not None:
            _terminate_process(agentd_proc)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} is required for the agentd tmux E2E test")


def _build_agentd(agentd_crate: Path) -> None:
    subprocess.run(
        ["cargo", "build", "--quiet"],
        cwd=agentd_crate,
        check=True,
    )


def _wait_for_agentd(
    client: AgentdClient,
    proc: subprocess.Popen[str],
    log_path: Path,
) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"doeff-agentd exited early with {proc.returncode}\n{_read_text(log_path)}"
            )
        try:
            client.status()
            return
        except OSError:
            time.sleep(0.1)
        except Exception:
            time.sleep(0.1)
    raise AssertionError(f"doeff-agentd did not become ready\n{_read_text(log_path)}")


def _write_fake_agent(path: Path) -> None:
    path.write_text(
        r"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

SESSION_ID = os.environ["DOEFF_RESULT_SESSION_ID"]
SOCKET = os.environ["DOEFF_AGENTD_SOCKET"]
AGENTD_BIN = os.environ["DOEFF_AGENTD_BIN"]
LOG_PATH = Path.cwd() / "fake-agent-events.jsonl"


def log(event: str, **data: object) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **data}, sort_keys=True) + "\n")


def render_idle() -> None:
    print("\u276f ", end="", flush=True)


def render_working() -> None:
    # A claude turn-activity bullet (\u23fa) so agentd clears its
    # awaiting_response latch. Crucially this is NOT a live spinner ("\u2026 (")
    # \u2014 a lingering spinner would read as an active marker and block
    # turn-end forever; a \u23fa bullet may stay on screen while idle.
    print("\n\u23fa Ran the task", flush=True)
    time.sleep(1.0)
    for _ in range(35):
        print("", flush=True)


def read_message() -> str | None:
    chunks: list[str] = []
    fd = sys.stdin.fileno()
    while True:
        timeout = 0.35 if chunks else None
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return "".join(chunks)
        data = os.read(fd, 4096)
        if not data:
            return None
        chunks.append(data.decode("utf-8", "replace"))


def report_invalid() -> str:
    # Report a SCHEMA-INVALID result over the agentd-owned report_result MCP
    # server (missing the required "ok" field). agentd must reject it
    # deterministically and MUST NOT re-prompt.
    proc = subprocess.Popen(
        [AGENTD_BIN, "report-result-mcp", "--session", SESSION_ID, "--socket", SOCKET],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    def rpc(msg):
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        return proc.stdout.readline()

    rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}})
    resp = ""
    for attempt in range(40):
        # Retry only the transient "not registered" race (the launch may not
        # have committed the session row yet); a schema rejection is terminal.
        resp = rpc({"jsonrpc": "2.0", "id": 2 + attempt, "method": "tools/call",
                    "params": {"name": "report_result",
                               "arguments": {"payload": {"summary": "missing ok"}}}})
        if "not registered" not in resp:
            break
        time.sleep(0.25)
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    return resp.strip()


print("fake interactive agent ready", flush=True)
render_idle()
while True:
    message = read_message()
    if message is None:
        break
    log("message", text=message)
    if "Produce the e2e structured result." in message:
        render_working()
        error = report_invalid()
        log("reported-invalid", error=error)
        # Do NOT report a valid result: end the turn at an idle prompt. The
        # session must fail on first occurrence with zero retries.
        render_idle()
    else:
        render_idle()
""".lstrip(),
        encoding="utf-8",
    )


def _read_session_db_state(db_path: Path, session_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT status, retries_used, result_payload_json
            FROM agent_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"session row not found: {session_id}")
        retry_events = conn.execute(
            """
            SELECT COUNT(*) AS retry_events
            FROM agent_session_events
            WHERE session_id = ? AND event_type = 'session_output_retry'
            """,
            (session_id,),
        ).fetchone()["retry_events"]
        rejected_events = conn.execute(
            """
            SELECT COUNT(*) AS rejected_events
            FROM agent_session_events
            WHERE session_id = ? AND event_type = 'session_result_rejected'
            """,
            (session_id,),
        ).fetchone()["rejected_events"]
        return {
            "status": row["status"],
            "retries_used": row["retries_used"],
            "result_payload_json": row["result_payload_json"],
            "retry_events": retry_events,
            "rejected_events": rejected_events,
        }
    finally:
        conn.close()


def _read_session_debug(db_path: Path, session_id: str) -> str:
    if not db_path.exists():
        return "<db missing>"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT status, retries_used, last_validation_error, awaiting_response,
                   observed_active_at, output_snippet
            FROM agent_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        events = conn.execute(
            """
            SELECT event_type, payload_json
            FROM agent_session_events
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (session_id,),
        ).fetchall()
        return json.dumps(
            {
                "session": dict(row) if row is not None else None,
                "recent_events": [
                    {
                        "event_type": event["event_type"],
                        "payload": json.loads(event["payload_json"]),
                    }
                    for event in events
                ],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    finally:
        conn.close()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _cleanup_tmux_session(session_name: str) -> None:
    if shutil.which("tmux") is None:
        return
    subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=5.0)
