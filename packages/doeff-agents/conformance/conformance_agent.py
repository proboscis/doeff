"""conformance-agent: the script-driven fake CLI agentd launches instead of
codex/claude (contract: conformance/README.md, C0-1).

Stdlib only. Two launch modes share this file:
  M1 (PATH-shadowing): installed as `claude`/`codex` on a test-owned PATH dir
     so agentd's real argv builders run; argv/env are journaled for asserts.
  M2 (command override): launched via an explicit `command=`.

Env contract:
  CONFORMANCE_SCRIPT   path to the JSON step array (required)
  CONFORMANCE_JOURNAL  path to the JSONL observation journal (required)
  DOEFF_RESULT_SESSION_ID / DOEFF_AGENTD_SOCKET / DOEFF_AGENTD_BIN
                       result-channel physics for report_result steps (M2);
                       in M1 the wired doeff_result MCP config in argv is
                       journaled instead of being spoken.

Steps (JSON objects, executed in order; see README for the frame vocabulary):
  {"render": "<frame-id>"} | {"render": {"literal": "..."}}
  {"await_keys": {"expect": "<substr>", "timeout_s": N}}
  {"await_monitor_ack": {"timeout_s": N}}
  {"report_result": {"payload": {...}}} | {"report_result": "schema_invalid"}
  {"sleep_s": N}
  {"scroll": N}
  {"record_env": ["NAME", ...]}
  {"exit": code}

After the script is exhausted the agent PARKS (keeps the pane process alive
reading stdin) — exiting would trip agentd's zombie/idle-shell reaper, which
is its own scenario (S19), never an accident.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

JOURNAL = Path(os.environ["CONFORMANCE_JOURNAL"])
SCRIPT = Path(os.environ["CONFORMANCE_SCRIPT"])

# Frames reproduce the VERBATIM substrings the Rust monitor classifies on
# (packages/doeff-agentd/src/main.rs:2775-3229). Frozen vocabulary — see
# README「凍結フレーム語彙」. Frames must go quiet after rendering: turn-end
# additionally requires a STABLE 500-char tail (main.rs:2832/2932).
FRAMES: dict[str, str] = {
    "F-idle-codex": "\n› ",
    "F-idle-claude": "\n❯ ",
    # codex active: "working (" / "esc to interrupt" (main.rs:3033)
    "F-active-codex": "\n• working (3s • esc to interrupt)\n",
    # claude active: spinner "… (" on the line above the last ❯ (main.rs:3071)
    "F-active-claude": "\n… (thinking)\n❯ ",
    # claude turn activity ⏺: clears awaiting_response, marks startup
    # finished, but is NOT an active marker (main.rs:3093) — safe to leave
    # on screen while idling (learned from agentd_result_retry_e2e_support).
    "F-turn-activity-claude": "\n⏺ Ran the task\n",
    "F-failed": "\nfatal error: conformance scripted failure\n",
    "F-failed-auth": "\nauthentication failed\n",
    "F-failed-timeout": "\nfatal error: operation timed out\n",
    "F-api-limit": "\nrate limit exceeded — weekly cap reached\n",
    "F-waiting": "\nType your message\n",
    # codex menu rendered with the idle glyph (the R6 core case)
    "F-menu-codex": (
        "\nApproaching rate limits\n"
        "› 1. Switch to smaller model\n"
        "  2. Keep current model\n"
        "Press enter to confirm\n"
    ),
    # neither idle nor active nor waiting: stall-watchdog bait
    "F-frozen": "\nPassword:\n",
    "F-trust-dialog": "\nDo you trust the files in this folder?\n ❯ 1. Yes, proceed\n",
    # R9 fast-path dialog frames: fill VERBATIM from the Rust detectors
    # (main.rs:3115/3150/3165/3176 + their tests at :4222-4406) before
    # enabling S18 — placeholders here would silently miss the detectors.
}


def journal(event: str, **data: object) -> None:
    with JOURNAL.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"event": event, "at": time.time(), **data}, sort_keys=True)
            + "\n"
        )


def render(spec: object) -> None:
    if isinstance(spec, dict):
        text = str(spec["literal"])
    else:
        text = FRAMES[str(spec)]
    print(text, end="", flush=True)
    journal("rendered", frame=spec)


def await_keys(expect: str, timeout_s: float) -> bool:
    """Accumulate tty input (tmux paste-buffer / send-keys) until `expect`
    appears or the deadline passes. Tolerates the confirm-resubmit extra
    Enters agentd may send (main.rs:2581)."""
    fd = sys.stdin.fileno()
    deadline = time.monotonic() + timeout_s
    got: list[str] = []
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.25)
        if not ready:
            continue
        data = os.read(fd, 4096)
        if not data:
            break
        got.append(data.decode("utf-8", "replace"))
        if expect in "".join(got):
            journal("keys", expect=expect, matched=True, text="".join(got))
            return True
    journal("keys", expect=expect, matched=False, text="".join(got))
    return False


def await_monitor_ack(timeout_s: float) -> bool:
    """Hold the currently-rendered frame until the monitor has CONSUMED it:
    poll `session.get` over the agentd socket until the session row exists
    and its `awaiting_response` latch has cleared (main.rs:3629 clears it
    only on an observed active marker / turn activity).

    Why a wire poll and not a sleep: `session.launch` upserts the session
    row only AFTER the prompt paste + Enter + confirm loop completes
    (main.rs:1794-1830, up to ~5s with confirm re-sends), and the monitor
    cannot observe a session that has no row. A frame rendered and retired
    inside that blind window never existed as far as agentd is concerned,
    so any sleep-based hold races the launch tail. This step is the
    deterministic sync point; scripts retire an active frame (scroll +
    next frame) only after it returns.
    """
    import socket as socket_mod

    session_id = os.environ["DOEFF_RESULT_SESSION_ID"]
    socket_path = os.environ["DOEFF_AGENTD_SOCKET"]
    deadline = time.monotonic() + timeout_s
    request_id = 0
    while time.monotonic() < deadline:
        request_id += 1
        line = ""
        try:
            with socket_mod.socket(
                socket_mod.AF_UNIX, socket_mod.SOCK_STREAM
            ) as sock:
                sock.settimeout(2.0)
                sock.connect(socket_path)
                request = {
                    "id": request_id,
                    "method": "session.get",
                    "params": {"session_id": session_id},
                }
                sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
                with sock.makefile("r", encoding="utf-8") as reader:
                    line = reader.readline()
        except OSError:
            # transient: daemon serves one connection at a time; a poll
            # colliding with the monitor's tick just tries again — a real
            # outage still fails loudly via the deadline below
            pass
        if line:
            response = json.loads(line)
            snapshot = response.get("result") if response.get("ok") else None
            if snapshot and not snapshot.get("awaiting_response", False):
                journal("monitor_ack", matched=True, status=snapshot.get("status"))
                return True
        time.sleep(0.1)
    journal("monitor_ack", matched=False)
    return False


def report_result(spec: object) -> None:
    """Speak the report_result MCP tool over agentd's own binary
    (report-result-mcp subcommand), exactly like a real CLI's MCP client.
    Retries ONLY the transient not-registered launch race; a schema
    rejection is terminal for that payload (ADR 0035 R4)."""
    if spec == "schema_invalid":
        payload: dict[str, object] = {"summary": "missing required field"}
    else:
        payload = spec["payload"]  # type: ignore[index]
    proc = subprocess.Popen(
        [
            os.environ["DOEFF_AGENTD_BIN"],
            "report-result-mcp",
            "--session",
            os.environ["DOEFF_RESULT_SESSION_ID"],
            "--socket",
            os.environ["DOEFF_AGENTD_SOCKET"],
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    def rpc(msg: dict[str, object]) -> str:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        return proc.stdout.readline()

    rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
    )
    resp = ""
    for attempt in range(40):
        resp = rpc(
            {
                "jsonrpc": "2.0",
                "id": 2 + attempt,
                "method": "tools/call",
                "params": {"name": "report_result", "arguments": {"payload": payload}},
            }
        )
        if "not registered" not in resp:
            break
        time.sleep(0.25)
    if proc.stdin is not None:
        proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    journal("report_result", spec=spec, response=resp.strip())


def park() -> None:
    journal("parked")
    fd = sys.stdin.fileno()
    while True:
        ready, _, _ = select.select([fd], [], [], 10.0)
        if ready and not os.read(fd, 4096):
            return


def main() -> None:
    journal("started", argv=sys.argv, cwd=os.getcwd())
    # Bottom-anchor the terminal before any frame: real CLIs render onto an
    # already-scrolled pane, so their text sits in the BOTTOM rows. tmux
    # capture-pane returns the full visible pane including blank rows below
    # the cursor, and the failure classifier only reads the last 10 lines
    # (main.rs:2956 output_tail_lower(output, 10)) — printing from the top
    # of a fresh pane leaves those tail rows blank and silently disables
    # tail-limited markers (observed: S8b classified blocked_api because
    # "fatal error" never entered the tail-10 window).
    print("\n" * 30, end="", flush=True)
    steps = json.loads(SCRIPT.read_text(encoding="utf-8"))
    for step in steps:
        if "render" in step:
            render(step["render"])
        elif "await_keys" in step:
            spec = step["await_keys"]
            await_keys(str(spec["expect"]), float(spec.get("timeout_s", 30)))
        elif "await_monitor_ack" in step:
            spec = step["await_monitor_ack"] or {}
            await_monitor_ack(float(spec.get("timeout_s", 30)))
        elif "report_result" in step:
            report_result(step["report_result"])
        elif "scroll" in step:
            # Retire stale rows the way a real TUI redraw does: codex's
            # "working (" status row is matched over the tail-30 window
            # (main.rs:3033) and our pane is append-only, so an old active
            # marker would otherwise pin the session active forever and
            # suppress turn-end.
            print("\n" * int(step["scroll"]), end="", flush=True)
            journal("scrolled", lines=step["scroll"])
        elif "sleep_s" in step:
            time.sleep(float(step["sleep_s"]))
        elif "record_env" in step:
            journal(
                "env",
                values={name: os.environ.get(name) for name in step["record_env"]},
            )
        elif "exit" in step:
            journal("exiting", code=step["exit"])
            sys.exit(int(step["exit"]))
        else:
            raise ValueError(f"unknown step: {step}")
    park()


if __name__ == "__main__":
    main()
