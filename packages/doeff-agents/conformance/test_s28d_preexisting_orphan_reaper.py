"""S28d pre-existing orphan reaper (contract README S28d, tag P, mode M2).

S28 (#580) closes the FUTURE hole: a daemon spawned WITH the env knob
`DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED=1` watches its supervisor and exits by
itself once orphaned. The knob rides spawn-time env, so it cannot retrofit
daemons that already exist — the 2026-07-30 host observation (25 leaked
`serve` processes, ALL ppid==1; 9 parked conformance_agent partners; 13
residual /tmp/agentd-conf-* dirs; ~250 polls/s standing load) stays forever
unless something converges the observed end state. S28d is that something:
a declarative reconciler in the harness fixture setup (AgentdHarness
`__enter__`) that

  (1) enumerates doeff-sessionhost `serve` processes whose --db lives under
      /tmp/agentd-conf-* AND whose ppid==1 (the supervisor's death is proven
      by the reparent to init),
  (2) reaps their launched non-adopted active sessions with the daemon's own
      cleanup semantics (wire `session.cleanup` — the mux session dies and
      the parked conformance_agent inside it dies WITH it, which ppid alone
      can never find: the agent's parent is the pane shell, not init),
  (3) terminates the daemon (graceful SIGTERM → lease release, SIGKILL
      backstop), and
  (4) sweeps residual /tmp/agentd-conf-* dirs that no live daemon serves
      (mtime grace protects a concurrent harness mid-setup).

Safety boundary, pinned by the counterexamples below:
  - ppid != 1 daemons (a concurrently RUNNING harness elsewhere — S16
    physics) are untouchable: a live supervisor is exactly what ppid proves.
  - production hosts (--db outside /tmp/agentd-conf-*, canonically
    ~/.local/state/doeff/agentd.sqlite) are excluded BY PATH regardless of
    ppid — launchd children have ppid==1 by construction, so ppid alone
    would misclassify every production host.
  - adopted seats are untouchable (mirror principle, ADR-DOE-AGENTS-007
    clause 3): cleanup is issued only for non-adopted rows.
"""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from doeff_agents.agentd_client import AgentdClient
from harness import (
    AGENT_SCRIPT,
    AgentdHarness,
    kill_session_out_of_band,
    require_binaries,
    resolve_agentd_bin,
    session_exists_out_of_band,
)

PROMPT = "park in the active state"


# -- synthetic-population helpers ---------------------------------------------


def _clean_daemon_env() -> dict[str, str]:
    """Env for synthesizing the PRE-EXISTING population: plain tmux physics,
    explicitly WITHOUT the S28 self-exit knob — that is the whole point (the
    leaked daemons predate the knob and can never receive it)."""
    env = dict(os.environ)
    env.pop("DOEFF_SESSIONHOST_BACKEND", None)
    env.pop("DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED", None)
    return env


def _daemon_argv(agentd_bin: Path, runtime_dir: Path) -> list[str]:
    return [
        str(agentd_bin),
        "--db",
        str(runtime_dir / "agentd.sqlite"),
        "--socket",
        str(runtime_dir / "agentd.sock"),
        "--monitor-interval-ms",
        "100",
        "--max-running",
        "4",
        "--prompt-judge-cmd",
        "",
        "serve",
    ]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ppid_of(pid: int) -> int | None:
    probe = subprocess.run(
        ["ps", "-p", str(pid), "-o", "ppid="],
        capture_output=True,
        text=True,
        check=False,
    )
    text = probe.stdout.strip()
    if probe.returncode != 0 or not text:
        return None
    return int(text)


def _eventually(cond, timeout_s: float, what: str) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"condition not reached within {timeout_s}s: {what}")


def _wait_daemon_ready(runtime_dir: Path) -> None:
    client = AgentdClient(runtime_dir / "agentd.sock", timeout=5.0)
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            client.status()
            return
        except Exception:
            time.sleep(0.1)
    log_path = runtime_dir / "agentd.log"
    log = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    raise AssertionError(f"synthetic daemon not ready\n{log}")


def _spawn_orphan_daemon(agentd_bin: Path, runtime_dir: Path) -> int:
    """Spawn a REAL doeff-sessionhost that is already orphaned: a short-lived
    /bin/sh launches it in the background and exits, so the daemon reparents
    to init (ppid==1) — the same physics as a SIGKILLed pytest supervisor."""
    quoted = " ".join(shlex.quote(a) for a in _daemon_argv(agentd_bin, runtime_dir))
    log = shlex.quote(str(runtime_dir / "agentd.log"))
    spawned = subprocess.run(
        ["/bin/sh", "-c", f"{quoted} >{log} 2>&1 & echo $!"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(runtime_dir),
        env=_clean_daemon_env(),
    )
    pid = int(spawned.stdout.strip())
    _eventually(
        lambda: _ppid_of(pid) == 1,
        10.0,
        f"daemon {pid} reparented to init (orphaned)",
    )
    _wait_daemon_ready(runtime_dir)
    return pid


def _launch_parked_active_session(runtime_dir: Path) -> tuple[str, int]:
    """Launch the M2 conformance agent so it renders the ACTIVE frame after
    the prompt and parks — the synthetic twin of the leaked
    daemon+conformance_agent pairs (an active, never-terminating seat whose
    pane process outlives any supervisor). Returns (session_id, pane_pid)."""
    script = [
        {"render": "F-idle-claude"},
        {"await_keys": {"expect": PROMPT, "timeout_s": 30}},
        {"render": "F-active-claude"},
    ]
    script_path = runtime_dir / "script-s28d.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    work_dir = runtime_dir / "work-s28d"
    work_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"conf-s28d-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    client = AgentdClient(runtime_dir / "agentd.sock", timeout=5.0)
    client.launch_session(
        session_id=session_id,
        session_name=session_id,
        agent_type="claude",
        work_dir=work_dir,
        command=f"{shlex.quote(sys.executable)} {shlex.quote(str(AGENT_SCRIPT))}",
        prompt=PROMPT,
        session_env={
            "CONFORMANCE_SCRIPT": str(script_path),
            "CONFORMANCE_JOURNAL": str(runtime_dir / "journal-s28d.jsonl"),
        },
    )
    pane_pid = int(
        subprocess.run(
            ["tmux", "list-panes", "-t", session_id, "-F", "#{pane_pid}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip().splitlines()[0]
    )
    return session_id, pane_pid


# -- the reconciler obligation (red before the reaper exists) -----------------


def test_s28d_preexisting_orphan_daemon_and_partner_agent_are_reaped() -> None:
    """Acceptance 1+2+6: after harness fixture setup, BOTH populations are
    gone — the ppid==1 conformance daemon AND its parked conformance_agent
    partner (via the daemon's cleanup semantics, session and pane together),
    plus the now-dead daemon's runtime dir."""
    require_binaries()
    agentd_bin = resolve_agentd_bin()
    runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-conf-", dir="/tmp"))
    daemon_pid: int | None = None
    session_id: str | None = None
    try:
        daemon_pid = _spawn_orphan_daemon(agentd_bin, runtime_dir)
        session_id, pane_pid = _launch_parked_active_session(runtime_dir)
        assert _pid_alive(daemon_pid), "synthetic orphan daemon must be running"
        assert _pid_alive(pane_pid), "parked conformance agent must be running"
        assert session_exists_out_of_band(session_id)

        # THE contract point: fixture setup of a fresh harness reconciles the
        # observed end state — no knob, no owner adjudication, no manual kill.
        with AgentdHarness():
            pass

        assert not _pid_alive(daemon_pid), (
            "pre-existing orphan daemon (ppid==1, --db under /tmp/agentd-conf-*)"
            " must be terminated by harness fixture setup"
        )
        assert not session_exists_out_of_band(session_id), (
            "the orphan daemon's launched session must be reaped with cleanup"
            " semantics before the daemon exits"
        )
        _eventually(
            lambda: not _pid_alive(pane_pid),
            10.0,
            "parked conformance_agent dies with its mux session",
        )
        assert not runtime_dir.exists(), (
            "the reaped daemon's runtime dir is residual garbage and must be swept"
        )
    finally:
        if session_id is not None:
            kill_session_out_of_band(session_id)
        if daemon_pid is not None and _pid_alive(daemon_pid):
            os.kill(daemon_pid, signal.SIGKILL)
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_s28d_residual_runtime_dirs_are_swept_with_grace() -> None:
    """Acceptance 6: /tmp/agentd-conf-* dirs with no live daemon are swept —
    but ONLY stale ones. A fresh dir may be a concurrent harness mid-setup
    (mkdtemp happened, daemon not spawned yet), so the mtime grace protects
    it: sweeping it would corrupt a live run, which is worse than leaving
    one dir for the next harness to sweep."""
    require_binaries()
    stale = Path(tempfile.mkdtemp(prefix="agentd-conf-", dir="/tmp"))
    fresh = Path(tempfile.mkdtemp(prefix="agentd-conf-", dir="/tmp"))
    try:
        past = time.time() - 3600.0
        os.utime(stale, (past, past))

        with AgentdHarness():
            pass

        assert not stale.exists(), (
            "a stale residual dir with no live daemon must be swept"
        )
        assert fresh.exists(), (
            "a fresh dir may belong to a concurrent harness mid-setup —"
            " the mtime grace must protect it"
        )
    finally:
        shutil.rmtree(stale, ignore_errors=True)
        shutil.rmtree(fresh, ignore_errors=True)


# -- counterexamples: the safety boundary -------------------------------------


def test_s28d_counterexample_live_supervisor_daemon_is_untouched() -> None:
    """Acceptance 3 (end-to-end half): a conformance daemon whose supervisor
    is ALIVE (ppid != 1 — this very pytest process) must survive another
    harness's fixture setup untouched, wire answering, dir intact. This is
    the concurrent-harness physics S16 relies on."""
    require_binaries()
    agentd_bin = resolve_agentd_bin()
    runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-conf-", dir="/tmp"))
    log = (runtime_dir / "agentd.log").open("a", encoding="utf-8")
    proc = subprocess.Popen(
        _daemon_argv(agentd_bin, runtime_dir),
        cwd=str(runtime_dir),
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        env=_clean_daemon_env(),
    )
    try:
        _wait_daemon_ready(runtime_dir)

        with AgentdHarness():
            pass

        assert proc.poll() is None, (
            "a daemon with a live supervisor (ppid != 1) must NOT be reaped"
        )
        AgentdClient(runtime_dir / "agentd.sock", timeout=5.0).status()
        assert runtime_dir.exists(), (
            "a live daemon's runtime dir must not be swept as residue"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10.0)
        log.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)


def test_s28d_counterexample_production_db_path_is_excluded_by_path() -> None:
    """Acceptance 3+4 (direct half): the classifier itself excludes the
    production sessionhost by PATH — ~/.local/state/doeff/agentd.sqlite is
    never a target even at ppid==1 (launchd children have ppid==1 by
    construction) — and the selector drops ppid != 1 rows even for a
    conformance path."""
    from harness import classify_conformance_daemon, select_reapable_orphans

    bin_path = "/usr/local/bin/doeff-sessionhost"
    production_db = Path.home() / ".local" / "state" / "doeff" / "agentd.sqlite"
    production_cmd = (
        f"{bin_path} --db {production_db}"
        " --socket /tmp/doeff-agentd.sock --monitor-interval-ms 1000 serve"
    )
    assert classify_conformance_daemon(production_cmd) is None, (
        "production sessionhost must be excluded by path regardless of ppid"
    )
    assert select_reapable_orphans([(4242, 1, production_cmd)]) == []

    conformance_cmd = (
        f"{bin_path} --db /tmp/agentd-conf-abc123/agentd.sqlite"
        " --socket /tmp/agentd-conf-abc123/agentd.sock serve"
    )
    target = classify_conformance_daemon(conformance_cmd)
    assert target is not None
    assert target.db_path == Path("/tmp/agentd-conf-abc123/agentd.sqlite")
    assert target.socket_path == Path("/tmp/agentd-conf-abc123/agentd.sock")

    # the console script is a shebang file: ps shows the interpreter at
    # argv[0] and doeff-sessionhost at argv[1] — the VERBATIM shape of the
    # live leaked population (measured 2026-07-31); note the ps-flattened
    # empty --prompt-judge-cmd argument
    shebang_cmd = (
        "/Users/u/repos/doeff/.venv/bin/python3"
        " /Users/u/repos/doeff/.venv/bin/doeff-sessionhost"
        " --db /tmp/agentd-conf-xyaqu22z/agentd.sqlite"
        " --socket /tmp/agentd-conf-xyaqu22z/agentd.sock"
        " --monitor-interval-ms 100 --max-running 4 --prompt-judge-cmd  serve"
    )
    shebang_target = classify_conformance_daemon(shebang_cmd)
    assert shebang_target is not None
    assert shebang_target.db_path == Path("/tmp/agentd-conf-xyaqu22z/agentd.sqlite")

    # ...but a production host in the same shebang shape stays excluded
    assert classify_conformance_daemon(
        "/Users/u/repos/doeff/.venv/bin/python3"
        " /Users/u/repos/doeff/.venv/bin/doeff-sessionhost"
        f" --db {production_db} --socket /tmp/doeff-agentd.sock serve"
    ) is None

    # a live supervisor (ppid != 1) protects even a conformance-path daemon
    assert select_reapable_orphans([(4242, 4241, conformance_cmd)]) == []

    reapable = select_reapable_orphans([(4242, 1, conformance_cmd)])
    assert [(pid, found.db_path) for pid, _cmd, found in reapable] == [
        (4242, Path("/tmp/agentd-conf-abc123/agentd.sqlite"))
    ]

    # a non-sessionhost process mentioning the dir (grep, editor) is no target
    assert classify_conformance_daemon(
        "grep --db /tmp/agentd-conf-abc123/agentd.sqlite serve"
    ) is None
