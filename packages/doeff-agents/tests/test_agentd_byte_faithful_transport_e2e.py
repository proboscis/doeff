"""Byte-faithful transport e2e (ADR 0035).

Pushes a structured result whose string values contain exactly the content
a fixed-width terminal grid would corrupt (word-boundary spaces, runs of
spaces, a trailing space) THROUGH A REAL TMUX PANE, then recovers it over
the agentd-owned `report_result` MCP data channel and asserts it is
byte-identical to what the agent emitted.

The fake agent, running in a real tmux pane:

  1. Prints the payload to the pane so its string values (which are longer
     than the terminal width) genuinely soft-wrap across the fixed-width
     grid — the same projection the retired scrape path recovered results
     from.

  2. Delivers the payload over the `report_result` stdio MCP server (a
     subcommand of the agentd binary), which relays it to agentd's
     `session.report_result` RPC. `await_result` then returns it verbatim.

The assertion is byte-level: the recovered summary keeps the exact runs of
spaces and the trailing space that a grid projection is prone to mangling.

The retired path's failure mode (a full-screen TUI padding cells, injecting
'.../ema/ pull/594' or dropping the space in 'ACPresult') needs a real
codex/claude renderer to reproduce and is documented in ADR 0035; it cannot
be reproduced by a non-TUI fake agent, so this test proves the positive
(byte-faithful) direction, which the removed `normalize_wrapped_json_strings`
heuristic could never guarantee.

Requires a built agentd binary and tmux; skipped otherwise.
"""

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

RESULT_BLOCK_BEGIN = "DOEFF_AGENT_RESULT_BEGIN"
RESULT_BLOCK_END = "DOEFF_AGENT_RESULT_END"

# String values chosen to exercise the corruption the fixed-width grid
# projection caused: a space at a likely wrap column, runs of spaces, and a
# trailing space. A word-boundary wrap loses the space; -J padding injects
# one. Only a data channel preserves them.
RESULT_PAYLOAD: dict[str, Any] = {
    "summary": (
        "ACPresult notevalidating value with  double  spaces and a long tail "
        "that will certainly wrap across the eighty column terminal grid boundary "
    ),
    "pr_url": "https://github.com/acme/proboscis-ema/pull/594",
}

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "pr_url"],
    "properties": {
        "summary": {"type": "string", "minLength": 1},
        "pr_url": {"type": "string", "minLength": 1},
    },
}


@pytest.mark.e2e
def test_agentd_report_result_is_byte_faithful_through_a_real_tmux_pane(
    tmp_path: Path,
) -> None:
    # Precondition: the payload is wider than a standard terminal, so it
    # genuinely traverses (soft-wraps across) the fixed-width grid.
    assert len(json.dumps(RESULT_PAYLOAD)) > 80

    result = _run_byte_faithful_e2e(tmp_path)

    # The payload recovered over the data channel is byte-identical to what
    # the agent emitted.
    assert result["await_status"] == "EXITED", result
    assert result["recovered_payload"] == RESULT_PAYLOAD, (
        "the result recovered over report_result must be byte-identical to what "
        f"the agent emitted:\n  emitted:   {RESULT_PAYLOAD}\n  recovered: {result['recovered_payload']}"
    )
    # Byte level: the exact whitespace a grid projection mangles survived.
    recovered_summary = result["recovered_payload"]["summary"]
    assert recovered_summary == RESULT_PAYLOAD["summary"]
    assert "  double  spaces" in recovered_summary, "runs of spaces must survive"
    assert recovered_summary.endswith(" "), "the trailing space must survive"

    # The persisted DB payload is byte-identical too (same source of truth).
    assert json.loads(result["db_result_payload_json"]) == RESULT_PAYLOAD
    assert result["session_status"] == "done"
    assert result["validation_error"] is None

    # The content genuinely reached the pane (it traversed the real grid).
    assert result["block_reached_pane"], (
        "the payload should have appeared in the real tmux pane"
    )


def _run_byte_faithful_e2e(tmp_path: Path) -> dict[str, Any]:
    _require_binary("cargo")
    _require_binary("tmux")

    packages_dir = Path(__file__).resolve().parents[2]
    agentd_crate = packages_dir / "doeff-agentd"
    subprocess.run(["cargo", "build", "--quiet"], cwd=agentd_crate, check=True)
    agentd_bin = agentd_crate / "target" / "debug" / "doeff-agentd"

    runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-byte-faithful-", dir="/tmp"))
    session_id = f"byte-faithful-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    script_path = runtime_dir / "fake_reporting_agent.py"
    _write_fake_agent(script_path)

    db_path = runtime_dir / "agentd.sqlite"
    socket_path = runtime_dir / "agentd.sock"
    agentd_log_path = runtime_dir / "agentd.log"
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script_path))}"

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
                # A command override drives our fake agent instead of real
                # claude. The agent reports the result itself over the stdio
                # MCP server (agentd does not wire it for command overrides).
                command=command,
                prompt="Produce the byte-faithful structured result.",
                lifecycle=AgentSessionLifecycle.RUN_TO_COMPLETION,
                session_env={
                    "DOEFF_RESULT_SESSION_ID": session_id,
                    "DOEFF_AGENTD_SOCKET": str(socket_path),
                    "DOEFF_AGENTD_BIN": str(agentd_bin),
                    "DOEFF_RESULT_PAYLOAD": json.dumps(RESULT_PAYLOAD),
                },
                expected_result={"payload_schema": RESULT_SCHEMA},
            )

            # Capture the pane a few times while the block is on screen, so we
            # can demonstrate the scrape corruption regardless of exact timing.
            pane_text = _capture_until_block(client, session_id, timeout_s=15.0)
            outcome = client.await_result(session_id, timeout_seconds=25.0)

        db = _read_session_db_state(db_path, session_id)
        return {
            "await_status": outcome.status.name,
            "recovered_payload": outcome.result,
            "validation_error": outcome.validation_error,
            "session_status": db["status"],
            "db_result_payload_json": db["result_payload_json"],
            "block_reached_pane": (
                RESULT_BLOCK_BEGIN in pane_text and RESULT_BLOCK_END in pane_text
            ),
        }
    finally:
        _cleanup_tmux_session(session_id)
        if agentd_proc is not None:
            _terminate_process(agentd_proc)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _write_fake_agent(path: Path) -> None:
    path.write_text(
        r"""
from __future__ import annotations

import json
import os
import subprocess
import time

SESSION_ID = os.environ["DOEFF_RESULT_SESSION_ID"]
SOCKET = os.environ["DOEFF_AGENTD_SOCKET"]
AGENTD_BIN = os.environ["DOEFF_AGENTD_BIN"]
PAYLOAD = json.loads(os.environ["DOEFF_RESULT_PAYLOAD"])

# Park at an idle prompt marker so agentd's REPL-idle heuristics are happy.
print("\u276f\u00a0", end="", flush=True)

# 1) Print the payload inside the legacy markers so the SAME content lands on
#    the fixed-width grid and soft-wraps (what the retired scrape path saw).
print("", flush=True)
print("DOEFF_AGENT_RESULT_BEGIN", flush=True)
print(json.dumps(PAYLOAD), flush=True)
print("DOEFF_AGENT_RESULT_END", flush=True)

# 2) Deliver the result over the agentd-owned report_result stdio MCP server.
proc = subprocess.Popen(
    [AGENTD_BIN, "report-result-mcp", "--session", SESSION_ID, "--socket", SOCKET],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)


def _rpc(msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    return proc.stdout.readline()


_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2024-11-05"}})
# A real agent boots for seconds before reporting, by which time agentd has
# long committed the session row. This instant fake agent can beat that
# commit, so retry until the session is registered (a transient, not a
# schema failure).
resp = ""
for attempt in range(40):
    resp = _rpc({"jsonrpc": "2.0", "id": 2 + attempt, "method": "tools/call",
                 "params": {"name": "report_result", "arguments": {"payload": PAYLOAD}}})
    if '"isError":true' not in resp.replace(" ", "") and "not registered" not in resp:
        break
    time.sleep(0.25)
print("\nreport_result response: " + resp.strip(), flush=True)
proc.stdin.close()
proc.wait(timeout=10)

# Keep the pane alive so the monitor can observe the reported result and the
# test can capture the pane; then reach turn-end at an idle prompt.
print("\u276f\u00a0", end="", flush=True)
time.sleep(60)
""".lstrip(),
        encoding="utf-8",
    )


def _capture_until_block(client: AgentdClient, session_id: str, timeout_s: float) -> str:
    """Capture the pane until the result block is visible; return the text."""
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            last = client.capture_session(session_id, lines=200)
        except Exception:
            last = ""
        if RESULT_BLOCK_BEGIN in last and RESULT_BLOCK_END in last:
            return last
        # Polling a real tmux pane via a real doeff-agentd subprocess; there
        # is no doeff effect clock here.
        time.sleep(0.2)  # nosemgrep: doeff-no-sleep-in-tests
    return last


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        pytest.skip(f"{name} is required for the byte-faithful transport E2E test")


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
        except Exception:
            # Polling a real doeff-agentd subprocess's readiness; there is no
            # doeff effect clock to control here.
            time.sleep(0.1)  # nosemgrep: doeff-no-sleep-in-tests
    raise AssertionError(f"doeff-agentd did not become ready\n{_read_text(log_path)}")


def _read_session_db_state(db_path: Path, session_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, result_payload_json FROM agent_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise AssertionError(f"session row not found: {session_id}")
        return {
            "status": row["status"],
            "result_payload_json": row["result_payload_json"],
        }
    finally:
        conn.close()


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
