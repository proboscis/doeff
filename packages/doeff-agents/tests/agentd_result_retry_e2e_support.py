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
from doeff_agents.effects import AgentSessionLifecycle, AwaitStatus

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "ok"],
    "properties": {
        "summary": {"type": "string"},
        "ok": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def run_agentd_tmux_result_retry_e2e(tmp_path: Path) -> dict[str, Any]:
    """Run a real agentd + tmux contract retry without a real LLM agent."""
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
                session_env={"FAKE_AGENT_SESSION_ID": session_id},
                expected_result={
                    "payload_schema": RESULT_SCHEMA,
                    "max_retries": 1,
                    "retry_prompt": "RETRY after validation failure: %REASON%",
                },
            )
            outcome = client.await_result(session_id, timeout_seconds=25.0)

        if outcome.status != AwaitStatus.EXITED:
            raise AssertionError(
                f"agentd E2E did not exit successfully: {outcome!r}\n"
                f"db:\n{_read_session_debug(db_path, session_id)}\n"
                f"agentd log:\n{_read_text(agentd_log_path)}\n"
                f"fake log:\n{_read_text(fake_log_path)}"
            )

        db_snapshot = _read_session_db_state(db_path, session_id)
        fake_events = _read_jsonl(fake_log_path)
        message_events = [event for event in fake_events if event.get("event") == "message"]
        return {
            "payload": outcome.result,
            "validation_error": outcome.validation_error,
            "session_status": db_snapshot["status"],
            "retries_used": db_snapshot["retries_used"],
            "retry_events": db_snapshot["retry_events"],
            "messages_seen": len(message_events),
            "retry_prompt_seen": any(
                "RETRY after validation failure" in str(event.get("text", ""))
                for event in message_events
            ),
            "initial_protocol_seen": any(
                "DOEFF_AGENT_RESULT_BEGIN" in str(event.get("text", "")) for event in message_events
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
import sys
import time
from pathlib import Path

SESSION_ID = os.environ["FAKE_AGENT_SESSION_ID"]
WORK_DIR = Path.cwd()
LOG_PATH = WORK_DIR / "fake-agent-events.jsonl"


def log(event: str, **data: object) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **data}, sort_keys=True) + "\n")


def render_idle() -> None:
    print("\u276f\u00a0", end="", flush=True)


def render_working() -> None:
    print("\n\u2722 Swooping\u2026 (1s \u00b7 thinking)", flush=True)
    time.sleep(2.0)
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


print("fake interactive agent ready", flush=True)
render_idle()
attempt = 0
while True:
    message = read_message()
    if message is None:
        break
    attempt += 1
    log("message", attempt=attempt, text=message)
    if "RETRY after validation failure" in message:
        render_working()
        payload = json.dumps({"summary": "fixed", "ok": True})
        log("returned-valid")
        print("DOEFF_AGENT_RESULT_BEGIN", flush=True)
        print(payload, flush=True)
        print("DOEFF_AGENT_RESULT_END", flush=True)
        print("returned valid result", flush=True)
        render_idle()
    elif "Produce the e2e structured result." in message:
        render_working()
        payload = json.dumps({"summary": "missing ok"})
        log("returned-invalid")
        print("DOEFF_AGENT_RESULT_BEGIN", flush=True)
        print(payload, flush=True)
        print("DOEFF_AGENT_RESULT_END", flush=True)
        print("returned invalid result", flush=True)
        render_idle()
    else:
        log("ignored-non-task-message")
        print("\nignored non-task message", flush=True)
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
        return {
            "status": row["status"],
            "retries_used": row["retries_used"],
            "result_payload_json": row["result_payload_json"],
            "retry_events": retry_events,
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
