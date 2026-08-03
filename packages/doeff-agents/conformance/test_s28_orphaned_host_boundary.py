"""S28 supervised host lifetime — out-of-band orphan boundary (README S28).

The leak this scenario pins (host-load-steward observation, 2026-07-30): the
harness spawns `doeff-sessionhost ... serve` as a plain child process. Its
teardown (`__exit__` -> kill sessions -> SIGTERM daemon -> rmtree) is correct,
but it lives INSIDE the pytest process — SIGKILL the driver and nothing is
left with the authority or the code path to stop the daemon. 28 daemons at a
10Hz monitor tick plus 9 parked conformance_agent.py processes had accumulated
over 4 days on the shared dev host (34 processes, 769MB RSS, ~250 polls/s).

The boundary under test is therefore OUT OF BAND of the fixture: the harness
hands the daemon `DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED=1` at spawn time, and
the daemon itself watches its spawning parent. When that supervisor vanishes
(getppid() changed — SIGKILL, hard crash, plain exit, anything), the daemon

  1. reaps every ACTIVE session it launched (non-adopted rows, cleanup
     semantics — killing the mux session takes the parked
     conformance_agent.py down with it; adopted seats are exempt, mirror
     principle: the daemon did not create those panes), and
  2. exits through the normal shutdown path (lease released).

Production is exempt by construction: the knob is opt-in and a launchd-owned
sessionhost never sets it — S28b pins that a knob-less daemon SURVIVES its
parent's death, the pre-existing restart/durability physics (S10/S15).

S28a runs the issue's acceptance counterexample end to end (harness + parked
agent, driver SIGKILLed); S28c isolates the serve-level mechanism without any
mux session (cheap, no tmux).
"""

import contextlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from harness import (
    CONFORMANCE_DIR,
    RESULT_SCHEMA,
    SESSIONHOST_BACKEND,
    AgentdHarness,
    kill_session_out_of_band,
    resolve_agentd_bin,
    session_exists_out_of_band,
)

ORPHAN_KNOB = "DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED"
PROMPT = "Hold this seat until the driver dies."

# The full counterexample needs: daemon ready (<=15s) + M2 launch (~5s) +
# SIGKILL + the bounded-disappearance window (30s) + teardown — the suite's
# global 60s budget is too tight for the red diagnostics to stay readable.
pytestmark = pytest.mark.timeout(150)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _descendants(pid: int) -> set[int]:
    out = subprocess.run(
        ["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False
    ).stdout
    kids = {int(tok) for tok in out.split() if tok.strip().isdigit()}
    found = set(kids)
    for kid in kids:
        found |= _descendants(kid)
    return found


def _tmux_pane_pids(session_id: str) -> list[int]:
    probe = subprocess.run(
        ["tmux", "list-panes", "-t", session_id, "-F", "#{pane_pid}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return []
    return [int(tok) for tok in probe.stdout.split() if tok.strip().isdigit()]


def _read_json_line(proc: subprocess.Popen, timeout_s: float) -> dict:
    """One JSON line from the child's stdout, or a loud diagnosis."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    deadline = time.monotonic() + timeout_s
    buffer = b""
    while time.monotonic() < deadline:
        if proc.poll() is not None and not buffer:
            raise AssertionError(
                f"child driver exited early rc={proc.returncode} before reporting"
            )
        ready, _, _ = select.select([fd], [], [], 0.25)
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        buffer += chunk
        if b"\n" in buffer:
            line = buffer.split(b"\n", 1)[0]
            return json.loads(line.decode("utf-8"))
    raise AssertionError(f"child driver never reported; partial={buffer!r}")


def _sigkill_and_reap(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=10)


def _terminate_pid(pid: int) -> None:
    """Best-effort SIGTERM -> SIGKILL for a non-child process."""
    if not _alive(pid):
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def test_s28a_daemon_and_agent_die_after_driver_sigkill() -> None:
    """Acceptance counterexample: SIGKILL the pytest-side driver (so the
    harness `__exit__` NEVER runs) and assert the daemon AND its parked
    conformance agent are gone within bounded time."""
    if SESSIONHOST_BACKEND == "herdr":
        pytest.skip(
            "the pane-pid probe below is tmux-native (the boundary itself is"
            " substrate-agnostic: the reap speaks Tmux* effects)"
        )
    stderr_fd, stderr_name = tempfile.mkstemp(prefix="s28a-child-stderr-", suffix=".log")
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child-harness"],
        cwd=str(CONFORMANCE_DIR),
        stdout=subprocess.PIPE,
        stderr=stderr_fd,
    )
    os.close(stderr_fd)
    info: dict | None = None
    try:
        info = _read_json_line(child, timeout_s=90.0)
        assert "error" not in info, (
            f"child driver failed to build the leak shape: {info}\n"
            + Path(stderr_name).read_text(encoding="utf-8", errors="replace")
        )
        daemon_pid = int(info["daemon_pid"])
        session_id = str(info["session_id"])

        pane_pids = _tmux_pane_pids(session_id)
        assert pane_pids, "expected a live pane hosting the parked conformance agent"
        agent_pids = set(pane_pids)
        for pid in pane_pids:
            agent_pids |= _descendants(pid)
        assert _alive(daemon_pid), "daemon must be alive before the fault injection"

        # The fault: the driver process (harness owner) dies without teardown.
        _sigkill_and_reap(child)

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            daemon_gone = not _alive(daemon_pid)
            agents_gone = all(not _alive(pid) for pid in agent_pids)
            session_gone = not session_exists_out_of_band(session_id)
            if daemon_gone and agents_gone and session_gone:
                break
            time.sleep(0.5)

        log_text = ""
        log_path = Path(str(info["runtime_dir"])) / "agentd.log"
        if log_path.exists():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        assert not _alive(daemon_pid), (
            "daemon survived its supervisor's SIGKILL beyond the bounded window"
            f" (pid {daemon_pid})\n{log_text}"
        )
        leaked = sorted(pid for pid in agent_pids if _alive(pid))
        assert not leaked, (
            f"conformance agent processes leaked with the daemon gone: {leaked}\n"
            f"{log_text}"
        )
        assert not session_exists_out_of_band(session_id), (
            f"mux session {session_id} leaked past the boundary\n{log_text}"
        )
    finally:
        # This test's red form IS the leak under repair — never leave it
        # behind on the host regardless of outcome.
        _sigkill_and_reap(child)
        if child.stdout is not None:
            child.stdout.close()
        if info is not None and "error" not in info:
            with contextlib.suppress(Exception):
                kill_session_out_of_band(str(info["session_id"]))
            _terminate_pid(int(info["daemon_pid"]))
            shutil.rmtree(str(info["runtime_dir"]), ignore_errors=True)
        os.unlink(stderr_name)


# Bare-daemon child: spawn `doeff-sessionhost serve` in an isolated runtime
# dir, wait for the socket, report the pid, then EXIT — orphaning the daemon.
# argv: <agentd_bin> <runtime_dir> <knob: 0|1>
_BARE_CHILD = r"""
import json, os, socket, subprocess, sys, time
agentd_bin, runtime_dir, knob = sys.argv[1], sys.argv[2], sys.argv[3]
env = {k: v for k, v in os.environ.items()
       if k != "DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED"}
if knob == "1":
    env["DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED"] = "1"
log = open(os.path.join(runtime_dir, "agentd.log"), "a")
proc = subprocess.Popen(
    [agentd_bin,
     "--db", os.path.join(runtime_dir, "agentd.sqlite"),
     "--socket", os.path.join(runtime_dir, "agentd.sock"),
     "serve"],
    stdout=log, stderr=subprocess.STDOUT, env=env,
)
deadline = time.monotonic() + 30.0
while time.monotonic() < deadline:
    if proc.poll() is not None:
        print(json.dumps({"error": f"daemon exited early rc={proc.returncode}"}),
              flush=True)
        sys.exit(1)
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(os.path.join(runtime_dir, "agentd.sock"))
        break
    except OSError:
        time.sleep(0.2)
    finally:
        probe.close()
else:
    print(json.dumps({"error": "daemon never became ready"}), flush=True)
    sys.exit(1)
print(json.dumps({"daemon_pid": proc.pid}), flush=True)
# exit WITHOUT touching the daemon: from its point of view the supervisor
# just vanished (same reparenting physics as a SIGKILLed pytest).
"""


def _spawn_bare_daemon_via_dying_parent(knob: str) -> tuple[int, str]:
    runtime_dir = tempfile.mkdtemp(prefix="agentd-conf-s28-", dir="/tmp")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            _BARE_CHILD,
            str(resolve_agentd_bin()),
            runtime_dir,
            knob,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    line = child.stdout.strip().splitlines()[-1] if child.stdout.strip() else "{}"
    info = json.loads(line)
    if "error" in info or child.returncode != 0:
        log_path = Path(runtime_dir) / "agentd.log"
        log_text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.exists()
            else ""
        )
        shutil.rmtree(runtime_dir, ignore_errors=True)
        raise AssertionError(
            f"bare-daemon child failed: {info} rc={child.returncode}\n"
            f"stderr={child.stderr}\n{log_text}"
        )
    return int(info["daemon_pid"]), runtime_dir


def test_s28b_without_knob_daemon_survives_parent_death() -> None:
    """Production pin (acceptance 3): a daemon spawned WITHOUT the knob keeps
    running after its parent dies — launchd-owned hosts are reparented by
    construction and must never self-evict."""
    daemon_pid, runtime_dir = _spawn_bare_daemon_via_dying_parent(knob="0")
    try:
        settle_deadline = time.monotonic() + 4.0
        while time.monotonic() < settle_deadline:
            assert _alive(daemon_pid), (
                "knob-less daemon exited after its parent died — production"
                " sessionhost physics (survive the spawner) was broken"
            )
            time.sleep(0.5)
    finally:
        _terminate_pid(daemon_pid)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_s28c_with_knob_daemon_self_evicts_after_parent_death() -> None:
    """Mechanism isolation: the knob alone (no harness, no sessions) makes
    serve exit within bounded time once its spawning parent is gone."""
    daemon_pid, runtime_dir = _spawn_bare_daemon_via_dying_parent(knob="1")
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if not _alive(daemon_pid):
                break
            time.sleep(0.5)
        log_path = Path(runtime_dir) / "agentd.log"
        log_text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.exists()
            else ""
        )
        assert not _alive(daemon_pid), (
            f"supervised daemon (pid {daemon_pid}) outlived its parent beyond"
            f" the bounded window\n{log_text}"
        )
        assert "supervisor vanished" in log_text, (
            "orphan exit must be loud in the daemon log so leaked-host triage"
            f" can attribute it\n{log_text}"
        )
    finally:
        _terminate_pid(daemon_pid)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def _child_harness_main() -> None:
    """Runs in a SEPARATE process: build the exact leak shape observed on the
    host — a live supervised daemon plus a parked conformance agent — then
    block until the parent test SIGKILLs us (`__exit__` deliberately never
    runs)."""
    harness = AgentdHarness(extra_serve_args=["--prompt-judge-cmd", ""])
    harness.__enter__()
    scenario = harness.scenario(
        "s28a",
        [
            {"render": "F-idle-claude"},
            {"await_keys": {"expect": PROMPT, "timeout_s": 60}},
            {"sleep_s": 3600},
        ],
    )
    scenario.launch_m2(
        prompt=PROMPT,
        expected_result={"payload_schema": RESULT_SCHEMA},
    )
    deadline = time.monotonic() + 60.0
    agent_live = False
    while time.monotonic() < deadline:
        if any(entry.get("event") == "keys" for entry in scenario.journal()):
            agent_live = True
            break
        time.sleep(0.1)
    if not agent_live:
        print(json.dumps({"error": "agent never received the prompt"}), flush=True)
        raise SystemExit(1)
    assert harness._proc is not None
    print(
        json.dumps(
            {
                "daemon_pid": harness._proc.pid,
                "runtime_dir": str(harness.runtime_dir),
                "session_id": scenario.session_id,
            }
        ),
        flush=True,
    )
    time.sleep(3600)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child-harness":
        _child_harness_main()
        raise SystemExit(0)
    raise SystemExit(f"unknown argv: {sys.argv[1:]}")
