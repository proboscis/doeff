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
    # an UNKNOWN startup dialog — deliberately matches NO R9 detector and
    # no idle/active marker (the `❯` highlight is indented). Represents the
    # CLI prompts that appear unpredictably and are not in the R9 set yet;
    # the launch must fail closed on these instead of degrading silently
    # (pasting the prompt into the dialog) — 2026-07-07 contract revision.
    "F-dialog-unknown": (
        "\n Help improve Claude Code\n"
        "\n"
        " Share anonymous usage data with Anthropic?\n"
        "\n"
        " ❯ 1. Yes, share usage data\n"
        "   2. Maybe later\n"
        "\n"
        " Enter to confirm · Esc to cancel\n"
    ),
    # claude workspace-trust gate: VERBATIM from a live claude CLI frame
    # (herdr demo-claude-2, 2026-07-07; only the workspace path line is
    # variable). Whole-capture detector ("yes, i trust this folder" +
    # "no, exit" + "enter to confirm" — the long question sentence reflows
    # with pane width so it is NOT a marker); default selection is option 1
    # (trust), so the dismisser sends a bare Enter. Launch path only — M1.
    "F-dialog-trust": (
        "\n Accessing workspace:\n"
        "\n"
        " /home/user\n"
        "\n"
        " Quick safety check: Is this a project you created or one you trust? (Like your own code,\n"
        " a well-known open source project, or work from your team). If not, take a moment to\n"
        " review what's in this folder first.\n"
        "\n"
        " Claude Code'll be able to read, edit, and execute files here.\n"
        "\n"
        " Security guide\n"
        "\n"
        " ❯ 1. Yes, I trust this folder\n"
        "   2. No, exit\n"
        "\n"
        " Enter to confirm · Esc to cancel\n"
    ),
    # R9 fast-path dialog frames — VERBATIM from the Rust detectors and
    # their unit tests (main.rs:3115/3150/3165/3176, tests :4222-4406).
    # codex update dialog: detector wants "update now" + "skip until next
    # version" + "press enter to continue" in the lowercased tail-10; the
    # `›` highlight on option 1 makes the dismisser send Down×2+Enter
    # (codex_update_dialog_down_steps_to_skip_until_next). Dismissed only
    # in wait_for_repl_idle (launch path) — S18 runs it in M1.
    "F-dialog-codex-update": (
        "\n  ✨ Update available! 0.134.0 -> 0.135.0\n"
        "\n"
        "  Release notes: https://github.com/openai/codex/releases/latest\n"
        "\n"
        "› 1. Update now (runs `npm install -g @openai/codex`)\n"
        "  2. Skip\n"
        "  3. Skip until next version\n"
        "\n"
        "  Press enter to continue\n"
    ),
    # claude bypass-permissions confirmation: whole-capture detector
    # ("bypass permissions mode" + "no, exit" + "yes, i accept" + "enter
    # to confirm"); dismissed Down+Enter (select "2. Yes, I accept").
    # The `❯` highlight is indented, so it never reads as the idle prompt
    # (that check is line-start anchored). launch path only — M1.
    "F-dialog-bypass": (
        "\n  WARNING: Claude Code running in Bypass Permissions mode\n"
        "\n"
        "  In Bypass Permissions mode, Claude Code will not ask for your approval\n"
        "  before running potentially dangerous commands.\n"
        "\n"
        "  ❯ 1. No, exit\n"
        "    2. Yes, I accept\n"
        "\n"
        "  Enter to confirm · Esc to cancel\n"
    ),
    # claude fullscreen-renderer opt-in: whole-capture detector; dismissed
    # Down+Enter (select "2. Not now"). launch path only — M1.
    "F-dialog-fullscreen": (
        "\n  Try the new fullscreen renderer?\n"
        "\n"
        "  · Flicker-free output\n"
        "  · Mouse support\n"
        "\n"
        "  ❯ 1. Yes, try it\n"
        "    2. Not now\n"
        "\n"
        "  Enter to confirm · Esc to cancel\n"
    ),
    # claude managed-settings approval: whole-capture detector ("managed
    # settings require approval" + "settings requiring approval");
    # dismissed with a bare Enter. The ONLY R9 dialog also handled in the
    # MONITOR loop (main.rs:3604) — the other three exist solely in
    # wait_for_repl_idle — so S18 exercises this one in M2 mid-session.
    "F-dialog-managed": (
        "\n Managed settings require approval\n"
        "\n"
        " Your organization has configured managed settings that could allow execution\n"
        " of arbitrary code or interception of your prompts and responses.\n"
        "\n"
        " Settings requiring approval:\n"
        "   · OTEL_EXPORTER_OTLP_LOGS_ENDPOINT\n"
    ),
}


def journal(event: str, **data: object) -> None:
    with JOURNAL.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"event": event, "at": time.time(), **data}, sort_keys=True)
            + "\n"
        )


# ADR-DOE-AGENTS-006 (S21): the conversation/transcript contract. Mirrors the
# REAL CLIs' conversation physics closely enough for the daemon's identity
# capture (claude --session-id) and post-boot discovery (codex rollout
# session_meta cwd-match; fork's CLI-minted identity):
#   claude: transcript = $CLAUDE_CONFIG_DIR/projects/<mangled realpath cwd>/
#     <uuid>.jsonl; `--session-id <uuid>` pins the identity, `--resume <id>`
#     continues that transcript, `--resume <id> --fork-session` mints a new
#     uuid whose transcript starts as a copy of the parent's.
#   codex: rollout = $CODEX_HOME/sessions/Y/M/D/rollout-<ts>-<uuid>.jsonl with
#     a first-line session_meta {payload:{id,cwd}}; `resume <id>` appends to
#     the existing rollout, `fork <id>` mints a new uuid and copies the parent
#     rollout body under the new identity.
# The "conversation" journal event carries mode/id/parent and the INHERITED
# transcript content — S21's context-preservation assert reads it.
CONVERSATION: dict[str, object] = {"mode": "none", "transcript": None}


def resolve_conversation() -> None:
    import re as re_mod
    import uuid as uuid_mod

    # M1 shims exec() this script, so argv[0] never carries the CLI name —
    # the shim exports CONFORMANCE_KIND instead. argv[0] stays as the M2 /
    # direct-invocation fallback.
    kind = os.environ.get("CONFORMANCE_KIND") or Path(sys.argv[0]).name
    if kind not in ("claude", "codex"):
        return
    argv = sys.argv[1:]

    def flag_value(flag: str) -> str | None:
        if flag not in argv:
            return None
        idx = argv.index(flag)
        return argv[idx + 1] if idx + 1 < len(argv) else None

    parent: str | None = None
    mode = "fresh"
    if kind == "claude":
        parent = flag_value("--resume")
        if parent is not None:
            mode = "fork" if "--fork-session" in argv else "resume"
    else:
        for sub in ("resume", "fork"):
            if sub in argv:
                idx = argv.index(sub)
                if idx + 1 < len(argv):
                    parent = argv[idx + 1]
                    mode = sub
                break

    inherited = ""
    if kind == "claude":
        config_dir = os.environ.get(
            "CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")
        )
        project_dir = (
            Path(config_dir)
            / "projects"
            / re_mod.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(os.getcwd()))
        )
        project_dir.mkdir(parents=True, exist_ok=True)
        if mode == "fresh":
            conv = flag_value("--session-id") or uuid_mod.uuid4().hex
            transcript = project_dir / f"{conv}.jsonl"
            transcript.write_text(
                json.dumps({"type": "meta", "id": conv}) + "\n", encoding="utf-8"
            )
        elif mode == "resume":
            conv = str(parent)
            transcript = project_dir / f"{conv}.jsonl"
            if transcript.exists():
                inherited = transcript.read_text(encoding="utf-8")
        else:
            conv = uuid_mod.uuid4().hex
            parent_file = project_dir / f"{parent}.jsonl"
            if parent_file.exists():
                inherited = parent_file.read_text(encoding="utf-8")
            transcript = project_dir / f"{conv}.jsonl"
            transcript.write_text(inherited, encoding="utf-8")
    else:
        codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
        stamp = time.strftime("%Y/%m/%d", time.gmtime())
        day_dir = Path(codex_home) / "sessions" / stamp
        day_dir.mkdir(parents=True, exist_ok=True)

        def find_rollout(conv_id: str) -> Path | None:
            hits = sorted(
                Path(codex_home).glob(f"sessions/*/*/*/rollout-*-{conv_id}.jsonl")
            )
            return hits[-1] if hits else None

        def meta_line(conv_id: str) -> str:
            return (
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": conv_id, "cwd": os.getcwd()},
                    }
                )
                + "\n"
            )

        ts = int(time.time() * 1000)
        if mode == "fresh":
            conv = uuid_mod.uuid4().hex
            transcript = day_dir / f"rollout-{ts}-{conv}.jsonl"
            transcript.write_text(meta_line(conv), encoding="utf-8")
        elif mode == "resume":
            conv = str(parent)
            existing = find_rollout(conv)
            if existing is not None:
                transcript = existing
                inherited = transcript.read_text(encoding="utf-8")
            else:
                transcript = day_dir / f"rollout-{ts}-{conv}.jsonl"
                transcript.write_text(meta_line(conv), encoding="utf-8")
        else:
            conv = uuid_mod.uuid4().hex
            parent_file = find_rollout(str(parent))
            if parent_file is not None:
                inherited = parent_file.read_text(encoding="utf-8")
            transcript = day_dir / f"rollout-{ts}-{conv}.jsonl"
            body = "".join(inherited.splitlines(keepends=True)[1:])
            transcript.write_text(meta_line(conv) + body, encoding="utf-8")

    CONVERSATION["mode"] = mode
    CONVERSATION["transcript"] = str(transcript)
    journal(
        "conversation",
        kind=kind,
        mode=mode,
        conversation_id=conv,
        parent=parent,
        inherited=inherited,
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
    outside the launch-owned BOOTING phase and its `awaiting_response` latch
    has cleared (only on an observed active marker / turn activity).

    Why a wire poll and not a sleep: `session.launch` upserts the session
    row before readiness/prompt delivery.  That initial row deliberately has
    `status=booting` and an unarmed latch while launch owns pane I/O.  This
    poll therefore waits for both launch hand-off and monitor consumption;
    scripts retire an active frame only after it returns.
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
            if (
                snapshot
                and snapshot.get("status") != "booting"
                and not snapshot.get("awaiting_response", False)
            ):
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
    # 200 attempts x 50ms = the same 10s total budget the previous
    # 40 x 250ms grid gave. Registration now precedes the ready gate, but a
    # report can still race the small new-session -> BOOTING-upsert interval.
    for attempt in range(200):
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
        # Retry grid must stay well inside one monitor tick (100ms under the
        # harness's compressed clock): the golden-path zero-solicitation pin
        # asserts "the report lands before the tick after the latch-clearing
        # observation", and a coarser grid (0.25s) phase-locks that outcome
        # to the mux backend's incidental subprocess latency — green on tmux,
        # red on the faster herdr substrate for the same protocol behavior
        # (measured 2026-07-07: report landed 8ms after tick1 on tmux vs
        # 121ms on herdr; both landed at the first grid point after row
        # registration). 50ms keeps the pin about the protocol, not the
        # backend's speed.
        time.sleep(0.05)
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
    # ADR-006 (S21): establish conversation identity + transcript before any
    # frame. A resumed/forked incarnation runs the CONFORMANCE_RESUME_SCRIPT
    # when provided — the fresh script and the revived script are different
    # acts of the same scenario.
    resolve_conversation()
    # Bottom-anchor the terminal before any frame: real CLIs render onto an
    # already-scrolled pane, so their text sits in the BOTTOM rows. tmux
    # capture-pane returns the full visible pane including blank rows below
    # the cursor, and the failure classifier only reads the last 10 lines
    # (main.rs:2956 output_tail_lower(output, 10)) — printing from the top
    # of a fresh pane leaves those tail rows blank and silently disables
    # tail-limited markers (observed: S8b classified blocked_api because
    # "fatal error" never entered the tail-10 window).
    print("\n" * 30, end="", flush=True)
    script_path = SCRIPT
    if CONVERSATION["mode"] in ("resume", "fork"):
        alt = os.environ.get("CONFORMANCE_RESUME_SCRIPT")
        if alt:
            script_path = Path(alt)
    steps = json.loads(script_path.read_text(encoding="utf-8"))
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
        elif "transcript_note" in step:
            # ADR-006 (S21): write a marker into the conversation transcript —
            # a revived incarnation proves context preservation by journaling
            # this marker back as `inherited`.
            transcript = CONVERSATION.get("transcript")
            if transcript:
                with open(str(transcript), "a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {"type": "note", "text": step["transcript_note"]}
                        )
                        + "\n"
                    )
            journal("transcript_note", text=step["transcript_note"])
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
