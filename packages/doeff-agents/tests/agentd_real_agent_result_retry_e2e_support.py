from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from doeff_agents.adapters.codex import trust_workspace_in_codex_home
from doeff_agents.agentd_client import AgentdClient
from doeff_agents.claude_home import prepare_claude_home
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

REAL_AGENT_TYPES = ("claude", "codex")
DEFAULT_REAL_CLAUDE_CONFIG_DIR = Path("~/.config/claude-nameissoap")
DEFAULT_REAL_CLAUDE_AUTH_EMAIL = "nameissoap@gmail.com"


def run_agentd_real_agent_result_report_e2e(
    tmp_path: Path,
    agent_type: str,
) -> dict[str, Any]:
    """Run a real Claude/Codex agent that delivers its result over the
    agentd-owned report_result MCP channel (ADR 0035).

    agentd wires the report_result stdio MCP server into the agent's launch
    automatically; the prompt tells the agent to call it. A valid report
    finalises the session as done — with zero re-prompt retries.
    """
    assert agent_type in REAL_AGENT_TYPES
    _require_live_binary("cargo")
    _require_live_binary("tmux")
    _require_live_binary(agent_type)

    packages_dir = Path(__file__).resolve().parents[2]
    agentd_crate = packages_dir / "doeff-agentd"
    _build_agentd(agentd_crate)

    runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-real-e2e-", dir="/tmp"))
    session_id = f"agentd-real-{agent_type}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    work_dir = tmp_path / f"work-{agent_type}"
    work_dir.mkdir()

    db_path = runtime_dir / "agentd.sqlite"
    socket_path = runtime_dir / "agentd.sock"
    agentd_log_path = runtime_dir / "agentd.log"
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
                    "250",
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
                agent_type=agent_type,
                work_dir=work_dir,
                prompt=_initial_prompt(agent_type),
                model="opus" if agent_type == "claude" else None,
                lifecycle=AgentSessionLifecycle.RUN_TO_COMPLETION,
                session_env=_session_env(agent_type, runtime_dir, work_dir),
                expected_result={"payload_schema": RESULT_SCHEMA},
            )
            outcome = client.await_result(session_id, timeout_seconds=240.0)

        if outcome.status != AwaitStatus.EXITED:
            raise AssertionError(
                f"agentd real-agent E2E did not exit successfully: {outcome!r}\n"
                f"db:\n{_read_session_debug(db_path, session_id)}\n"
                f"agentd log:\n{_read_text(agentd_log_path)}\n"
                f"capture:\n{_capture_tmux(session_id)}"
            )
        if outcome.validation_error is not None or outcome.result is None:
            raise AssertionError(
                f"agentd real-agent E2E returned invalid result: {outcome!r}\n"
                f"db:\n{_read_session_debug(db_path, session_id)}\n"
                f"agentd log:\n{_read_text(agentd_log_path)}\n"
                f"capture:\n{_capture_tmux(session_id)}"
            )

        db_snapshot = _read_session_db_state(db_path, session_id)
        return {
            "payload": outcome.result,
            "validation_error": outcome.validation_error,
            "session_status": db_snapshot["status"],
            "retries_used": db_snapshot["retries_used"],
            "retry_events": db_snapshot["retry_events"],
            "result_payload_json": db_snapshot["result_payload_json"],
        }
    finally:
        _cleanup_tmux_session(session_id)
        if agentd_proc is not None:
            _terminate_process(agentd_proc)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _initial_prompt(agent_type: str) -> str:
    fixed = _fixed_summary(agent_type)
    return (
        "Live agentd report_result E2E. Your only task is to deliver a result. "
        "Call the `report_result` MCP tool exactly once with this exact payload "
        "argument (a JSON object, not a string):\n"
        f'payload = {{"summary": "{fixed}", "ok": true}}\n'
        "Do not print the result to the terminal and do not create result files; "
        "deliver it ONLY through the report_result tool. After the tool confirms "
        "the result was recorded, end your turn at the interactive prompt. Do not "
        "run sleep, background terminals, loops, or long-running commands."
    )


def _fixed_summary(agent_type: str) -> str:
    return f"fixed by {agent_type}"


def _session_env(agent_type: str, _runtime_dir: Path, work_dir: Path) -> dict[str, str]:
    if agent_type == "claude":
        claude_config_dir = _prepare_real_claude_home(work_dir)
        real_home = str(Path.home())
        return {
            "CLAUDE_CONFIG_DIR": str(claude_config_dir),
            "DISABLE_AUTO_UPDATE": "true",
            "DISABLE_UPDATE_PROMPT": "true",
            "HOME": real_home,
        }
    if agent_type == "codex":
        codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        trust_workspace_in_codex_home(codex_home, work_dir)
        return {"CODEX_HOME": codex_home}
    raise AssertionError(f"unsupported real agent type: {agent_type}")


def _prepare_real_claude_home(work_dir: Path) -> Path:
    claude_config_dir = _real_claude_config_dir()
    claude_json = claude_config_dir / ".claude.json"
    expected_email = _real_claude_auth_email()
    assert claude_json.exists(), (
        f"Claude Code authentication is required at {claude_json}. "
        f"Log in as {expected_email} before this E2E test."
    )
    _assert_real_claude_auth(claude_json, expected_email)
    prepare_claude_home(claude_config_dir, (work_dir,))
    for auth_path in (
        claude_config_dir / ".claude.json",
        claude_config_dir / ".claude" / ".claude.json",
    ):
        _assert_real_claude_auth(auth_path, expected_email)
    return claude_config_dir


def _real_claude_config_dir() -> Path:
    configured = os.environ.get("DOEFF_AGENTS_REAL_CLAUDE_CONFIG_DIR") or os.environ.get(
        "DOEFF_AGENTS_PERSONAL_CLAUDE_CONFIG_DIR"
    )
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_REAL_CLAUDE_CONFIG_DIR.expanduser()


def _real_claude_auth_email() -> str:
    return os.environ.get(
        "DOEFF_AGENTS_REAL_CLAUDE_AUTH_EMAIL",
        DEFAULT_REAL_CLAUDE_AUTH_EMAIL,
    )


def _assert_real_claude_auth(claude_json: Path, expected_email: str) -> None:
    data = json.loads(claude_json.read_text(encoding="utf-8"))
    email = data.get("oauthAccount", {}).get("emailAddress")
    assert email == expected_email, (
        f"Claude Code auth for real-agent E2E must be {expected_email}; {claude_json} has {email!r}"
    )


def _require_live_binary(name: str) -> None:
    assert shutil.which(name) is not None, (
        f"{name!r} is required for the real-agent result retry E2E test"
    )


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
            _poll_pause(0.1)
        except Exception:
            _poll_pause(0.1)
    raise AssertionError(f"doeff-agentd did not become ready\n{_read_text(log_path)}")


def _poll_pause(seconds: float) -> None:
    threading.Event().wait(seconds)


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
        return f"db not found: {db_path}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT status, retries_used, last_validation_error, terminal_cause_json,
                   output_snippet, result_payload_json
            FROM agent_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        session_debug = dict(row) if row is not None else {"missing_session": session_id}
        events = conn.execute(
            """
            SELECT event_type, payload_json
            FROM agent_session_events
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 5
            """,
            (session_id,),
        ).fetchall()
        event_debug = [
            {
                "event_type": event["event_type"],
                "payload": _compact_event_payload(event["payload_json"]),
            }
            for event in events
        ]
        return json.dumps(
            {"session": session_debug, "recent_events": event_debug},
            ensure_ascii=False,
            indent=2,
        )
    finally:
        conn.close()


def _compact_event_payload(raw_payload: str) -> dict[str, Any]:
    payload = json.loads(raw_payload)
    if isinstance(payload, dict) and isinstance(payload.get("output_snippet"), str):
        payload["output_snippet"] = payload["output_snippet"][-1200:]
    return payload


def _capture_tmux(session_name: str) -> str:
    completed = subprocess.run(
        ["tmux", "capture-pane", "-t", session_name, "-p", "-S", "-200"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout + completed.stderr


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _cleanup_tmux_session(session_name: str) -> None:
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
