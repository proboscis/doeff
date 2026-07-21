"""Driver harness for the agentd conformance suite (contract: README.md).

Dependency discipline (mirrors ACP mini_conformance): the driver may import
ONLY the public wire client (doeff_agents.agentd_client / effects enums),
pytest, and stdlib. SQLite access is READ-ONLY and reserved for obligations
that do not appear on the wire (payload persistence, counter durability).

Absorbed from tests/agentd_result_retry_e2e_support.py — the proven physics:
cargo-built agentd, 100ms monitor tick, fake CLI in a real tmux pane,
result channel spoken via `report-result-mcp`.
"""

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from doeff_agents.agentd_client import AgentdClient

CONFORMANCE_DIR = Path(__file__).resolve().parent
PACKAGES_DIR = CONFORMANCE_DIR.parents[1]
AGENTD_CRATE = PACKAGES_DIR / "doeff-agentd"
AGENT_SCRIPT = CONFORMANCE_DIR / "conformance_agent.py"
JUDGE_SCRIPT = CONFORMANCE_DIR / "scripted_judge.py"

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "ok"],
    "properties": {
        "summary": {"type": "string"},
        "ok": {"type": "boolean"},
    },
    "additionalProperties": False,
}


# herdr trial transfer gate: DOEFF_SESSIONHOST_BACKEND=herdr flows through the
# harness environment into the daemon under test (parse_args reads it), and the
# harness swaps its own backend-dependent physics (required binary, out-of-band
# kill) to the herdr equivalents. Observations live in herdr-physics.md — the
# frozen contract README is tmux-oracle and stays untouched.
SESSIONHOST_BACKEND = os.environ.get("DOEFF_SESSIONHOST_BACKEND", "tmux")


def require_binaries() -> None:
    # Under CONFORMANCE_AGENTD_BIN (see build_agentd) the daemon under test
    # is not cargo-built, so cargo is not a prerequisite.
    mux = "herdr" if SESSIONHOST_BACKEND == "herdr" else "tmux"
    names = (mux,) if os.environ.get("CONFORMANCE_AGENTD_BIN") else ("cargo", mux)
    for name in names:
        if shutil.which(name) is None:
            pytest.skip(f"{name} is required for the agentd conformance suite")


def _herdr_call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """One-shot newline-JSON RPC to the herdr socket (out-of-band fixtures).

    Returns the raw envelope ({"result": ...} or {"error": ...}); callers
    decide whether an error is swallowed (best-effort kill) or fatal
    (fault-injection setup that the test depends on).
    """
    import socket as socket_mod

    herdr_socket = os.environ.get(
        "DOEFF_SESSIONHOST_HERDR_SOCKET",
        os.path.join(os.path.expanduser("~"), ".config", "herdr", "herdr.sock"),
    )
    line = json.dumps({"id": "conf-oob", "method": method, "params": params})
    sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    try:
        sock.settimeout(5.0)
        sock.connect(herdr_socket)
        sock.sendall((line + "\n").encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
    finally:
        sock.close()
    return json.loads(b"".join(chunks).decode("utf-8").strip())


def kill_session_out_of_band(session_id: str) -> None:
    """Backend-aware out-of-band session kill (S9 + harness teardown).

    tmux: `tmux kill-session -t NAME`. herdr: resolve the agent name to its
    pane over the socket (`agent.get {target}`) and `pane.close` it — herdr
    has no name-addressed close. Both paths swallow "not found": the kill is
    best-effort teardown / S9 fault injection, not an assertion.
    """
    if SESSIONHOST_BACKEND != "herdr":
        subprocess.run(
            ["tmux", "kill-session", "-t", session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        got = _herdr_call("agent.get", {"target": session_id})
        if "error" in got:
            return
        _herdr_call("pane.close", {"pane_id": got["result"]["agent"]["pane_id"]})
    except OSError:
        return


def session_exists_out_of_band(session_id: str) -> bool:
    """Backend-aware out-of-band liveness probe (load-bearing for cleanup
    asserts: a rejected/failed launch must leave no mux session behind).

    tmux: `tmux has-session -t NAME`. herdr: `agent.get {target}` resolves
    the name; an error envelope means the agent/pane does not exist.
    """
    if SESSIONHOST_BACKEND != "herdr":
        probe = subprocess.run(
            ["tmux", "has-session", "-t", session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return probe.returncode == 0
    try:
        got = _herdr_call("agent.get", {"target": session_id})
    except OSError:
        return False
    return "error" not in got


def break_pane_observation_out_of_band(session_id: str, pane_id: str) -> None:
    """Kill the MONITORED PANE while keeping session liveness true (S19c
    stale-observation fault injection): capture of the recorded pane_id must
    start failing while the liveness probe keeps answering, so the monitor
    reaches the stale reaper instead of the `lost` path.

    tmux: add a second window to the session, then kill the monitored pane —
    the session survives through the new window (`has-session` true).

    herdr: agent == pane (two layers, not tmux's session>window>pane three),
    so a bare pane.close would delete the agent entry too and the liveness
    check (agent.get) would fail first. Synthesize the same split state:
    split a sibling pane, re-report the agent name onto the sibling
    (pane.report_agent — same namespace as agent.start, measured 2026-07-07),
    then close the original pane. agent.get then resolves to the sibling
    (alive) while pane.read on the recorded pane_id fails. Errors raise:
    this is fault-injection setup the test depends on, not best-effort.
    """
    if SESSIONHOST_BACKEND != "herdr":
        subprocess.run(
            ["tmux", "new-window", "-t", session_id],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["tmux", "kill-pane", "-t", pane_id],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    split = _herdr_call("pane.split", {"pane_id": pane_id, "direction": "right"})
    if "error" in split:
        raise RuntimeError(f"herdr pane.split failed: {split['error']}")
    sibling = split["result"]["pane"]["pane_id"]
    reported = _herdr_call(
        "pane.report_agent",
        {
            "pane_id": sibling,
            "source": "doeff-conformance",
            "agent": session_id,
            "state": "idle",
        },
    )
    if "error" in reported:
        raise RuntimeError(f"herdr pane.report_agent failed: {reported['error']}")
    closed = _herdr_call("pane.close", {"pane_id": pane_id})
    if "error" in closed:
        raise RuntimeError(f"herdr pane.close failed: {closed['error']}")


def create_session_out_of_band(name: str, *, cwd: str | None = None) -> str:
    """Create a live mux session OUTSIDE the daemon (S23-S27 adopt fixtures).

    The adopted seat's physics: a pane that already exists and that the
    daemon did not launch. Returns the substrate-native pane reference
    (`substrate.ref` for session.adopt / the turn descriptor's pane_id).

    tmux: a detached session running the user's shell. herdr: the same
    workspace.create -> agent.start -> root pane.close dance the sessionhost
    herdr substrate performs (observed physics, substrate_herdr.hy).
    """
    workdir = cwd or os.environ.get("HOME", "/tmp")
    if SESSIONHOST_BACKEND != "herdr":
        created = subprocess.run(
            ["tmux", "new-session", "-d", "-s", name, "-c", workdir,
             "-P", "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
            check=True,
        )
        return created.stdout.strip()
    ws = _herdr_call("workspace.create", {"label": name, "focus": False})
    if "error" in ws:
        raise RuntimeError(f"herdr workspace.create failed: {ws['error']}")
    ws_id = ws["result"]["workspace"]["workspace_id"]
    root_pane = ws["result"]["root_pane"]["pane_id"]
    started = _herdr_call(
        "agent.start",
        {
            "name": name,
            "cwd": workdir,
            "argv": [os.environ.get("SHELL", "/bin/sh")],
            "env": {},
            "workspace_id": ws_id,
            "focus": False,
        },
    )
    if "error" in started:
        raise RuntimeError(f"herdr agent.start failed: {started['error']}")
    closed = _herdr_call("pane.close", {"pane_id": root_pane})
    if "error" in closed:
        raise RuntimeError(f"herdr pane.close failed: {closed['error']}")
    return started["result"]["agent"]["pane_id"]


def build_agentd() -> Path:
    """Resolve the daemon binary under test.

    Transfer-gate seam (C3): `CONFORMANCE_AGENTD_BIN` points the whole suite
    at an alternative agentd-compatible executable (a single binary path —
    it is reused verbatim as `DOEFF_AGENTD_BIN` for the `report-result-mcp`
    relay, so it must implement the full agentd CLI contract: `serve` with
    the flags below plus the `report-result-mcp` subcommand).  Unset, the
    suite builds and runs the Rust oracle exactly as before — the seam is
    infra, not a contract change.
    """
    override = os.environ.get("CONFORMANCE_AGENTD_BIN")
    if override:
        path = Path(override)
        if not (path.exists() and os.access(path, os.X_OK)):
            raise AssertionError(
                f"CONFORMANCE_AGENTD_BIN is not an executable file: {path}"
            )
        return path
    subprocess.run(["cargo", "build", "--quiet"], cwd=AGENTD_CRATE, check=True)
    return AGENTD_CRATE / "target" / "debug" / "doeff-agentd"


@dataclass
class AgentdHarness:
    """One scenario = one isolated agentd (own root/db/socket/tmp homes)."""

    extra_serve_args: list[str] = field(default_factory=list)
    # Extra environment for the DAEMON process itself. Two uses: (1) the
    # env-var testability knobs that have no CLI flag
    # (DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS, DOEFF_AGENTD_STALE_OBSERVATION_SECS,
    # S19); (2) pointing the daemon-env fallbacks of the pre-launch trust
    # writers at scenario tmp dirs — trust_codex_workspace reads CODEX_HOME
    # from the DAEMON env (main.rs:1553), and trust_claude_workspace falls
    # back to it when session_env carries no CLAUDE_CONFIG_DIR
    # (main.rs:1500), so an unset value would write trust entries into the
    # operator's real ~/.codex / ~/.claude during a test run.
    extra_env: dict[str, str] = field(default_factory=dict)
    runtime_dir: Path = field(init=False)
    agentd_bin: Path = field(init=False)
    db_path: Path = field(init=False)
    socket_path: Path = field(init=False)
    log_path: Path = field(init=False)
    client: AgentdClient = field(init=False)
    _proc: subprocess.Popen[str] | None = field(init=False, default=None)
    _sessions: list[str] = field(init=False, default_factory=list)

    def __enter__(self) -> "AgentdHarness":
        require_binaries()
        self.agentd_bin = build_agentd()
        self.runtime_dir = Path(tempfile.mkdtemp(prefix="agentd-conf-", dir="/tmp"))
        self.db_path = self.runtime_dir / "agentd.sqlite"
        self.socket_path = self.runtime_dir / "agentd.sock"
        self.log_path = self.runtime_dir / "agentd.log"
        self.start()
        return self

    def start(self) -> None:
        log = self.log_path.open("a", encoding="utf-8")
        # The daemon's DEFAULT prompt judge is a REAL one-shot claude
        # subprocess (print mode, model haiku), running at every turn-end judgment point
        # before solicitation (main.rs:150/3722). Left enabled it burns
        # real quota and adds up to 3x45s of latency per scenario — the
        # suite's non-goal. Disable it unless the scenario wires the
        # scripted judge explicitly via extra_serve_args.
        judge_args = (
            []
            if "--prompt-judge-cmd" in self.extra_serve_args
            else ["--prompt-judge-cmd", ""]
        )
        self._proc = subprocess.Popen(
            [
                str(self.agentd_bin),
                "--db",
                str(self.db_path),
                "--socket",
                str(self.socket_path),
                "--monitor-interval-ms",
                "100",
                "--max-running",
                "4",
                *judge_args,
                *self.extra_serve_args,
                "serve",
            ],
            cwd=AGENTD_CRATE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, **self.extra_env} if self.extra_env else None,
        )
        self.client = AgentdClient(self.socket_path, timeout=5.0)
        self._wait_ready()

    def restart(self) -> None:
        """Durability probe (S10/S15): bounce the daemon, keep db + sessions.

        The daemon holds a DB lease with a 10s TTL (LEASE_TTL_SECONDS,
        main.rs:21) and does NOT release it on SIGTERM — a fresh `serve`
        started inside that window exits early with "lease is active"
        (acquire_lease_in_transaction, main.rs:1092). Retry past the TTL
        so restarts are deterministic; discovered by the S10 worker.
        """
        self._terminate()
        deadline = time.monotonic() + 15.0
        while True:
            try:
                self.start()
                return
            except AssertionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1.0)

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            assert self._proc is not None
            if self._proc.poll() is not None:
                raise AssertionError(
                    f"doeff-agentd exited early rc={self._proc.returncode}\n"
                    + self.log_text()
                )
            try:
                self.client.status()
                return
            except Exception:
                time.sleep(0.1)
        raise AssertionError(f"doeff-agentd not ready\n{self.log_text()}")

    # -- scenario plumbing -------------------------------------------------

    def scenario(self, name: str, script: list[dict[str, Any]]) -> "Scenario":
        work_dir = self.runtime_dir / f"work-{name}"
        work_dir.mkdir(parents=True, exist_ok=True)
        script_path = self.runtime_dir / f"script-{name}.json"
        journal_path = self.runtime_dir / f"journal-{name}.jsonl"
        script_path.write_text(json.dumps(script), encoding="utf-8")
        session_id = f"conf-{name}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._sessions.append(session_id)
        return Scenario(
            harness=self,
            session_id=session_id,
            work_dir=work_dir,
            script_path=script_path,
            journal_path=journal_path,
        )

    def adopt_fixture_session(self, name: str) -> str:
        """Create an out-of-band mux session and register it for teardown.

        Returns the substrate-native pane reference (substrate.ref)."""
        pane_ref = create_session_out_of_band(name)
        self._sessions.append(name)
        return pane_ref

    def substrate_kind(self) -> str:
        """The substrate binding kind this suite run speaks (koine session
        surface v0: substrate = {kind, ref})."""
        return "herdr" if SESSIONHOST_BACKEND == "herdr" else "tmux"

    # -- read-only observation ---------------------------------------------

    def session_rows_by_name(self, session_name: str) -> list[dict[str, Any]]:
        """All rows registered under a session_name (adopt ordering asserts:
        a failed adopt must leave NO row behind)."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM agent_sessions WHERE session_name = ?",
                (session_name,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def session_row(self, session_id: str) -> dict[str, Any]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise AssertionError(f"session row not found: {session_id}")
            return dict(row)
        finally:
            conn.close()

    def events(self, session_id: str) -> list[dict[str, Any]]:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT event_type, payload_json FROM agent_session_events"
                " WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            return [
                {"event_type": r["event_type"], "payload": json.loads(r["payload_json"])}
                for r in rows
            ]
        finally:
            conn.close()

    def log_text(self) -> str:
        if not self.log_path.exists():
            return ""
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    # -- teardown ------------------------------------------------------------

    def _terminate(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5.0)

    def __exit__(self, *exc: object) -> None:
        for session_id in self._sessions:
            kill_session_out_of_band(session_id)
        self._terminate()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)


# ADR-DOE-AGENTS-004 R9: the launch wire is auth-blind — auth/profile
# material rides the typed `binding` field and the launch admission
# rejects binding-owned keys (CODEX_HOME / CLAUDE_CONFIG_DIR) inside
# session_env.  The suite's scenarios still express identity as env
# pairs (that IS the physics the agent process observes), so the launch
# helpers convert the auth key for the launched agent_type into the
# binding; everything else stays a non-auth overlay.
_BINDING_ENV_KEY_BY_AGENT_TYPE = {
    "codex": ("CODEX_HOME", "codex", "codex_home"),
    "claude": ("CLAUDE_CONFIG_DIR", "claude-code", "config_dir"),
}


def binding_from_session_env(
    agent_type: str, session_env: dict[str, str]
) -> dict[str, Any] | None:
    """Pop the agent_type's auth env key out of session_env into a typed
    wire binding (None when the scenario declares no identity)."""
    spec = _BINDING_ENV_KEY_BY_AGENT_TYPE.get(agent_type)
    if spec is None:
        return None
    env_key, kind, field = spec
    value = session_env.pop(env_key, None)
    if value is None:
        return None
    return {"kind": kind, field: value}


@dataclass
class Scenario:
    harness: AgentdHarness
    session_id: str
    work_dir: Path
    script_path: Path
    journal_path: Path

    def launch_m2(
        self,
        *,
        agent_type: str = "claude",
        prompt: str,
        expected_result: dict[str, Any] | None = None,
        extra_env: dict[str, str] | None = None,
        lifecycle: Any = None,
    ) -> None:
        """M2 (command override): the conformance agent runs as the pane
        command; the result channel is spoken via report-result-mcp.

        lifecycle defaults to RUN_TO_COMPLETION; S26 passes INTERACTIVE to
        put the koine reap exemption (safety clause 1) under test."""
        from doeff_agents.adapters.base import AgentSessionLifecycle

        if lifecycle is None:
            lifecycle = AgentSessionLifecycle.RUN_TO_COMPLETION
        command = (
            f"{shlex.quote(sys.executable)} {shlex.quote(str(AGENT_SCRIPT))}"
        )
        session_env = {
            "CONFORMANCE_SCRIPT": str(self.script_path),
            "CONFORMANCE_JOURNAL": str(self.journal_path),
            "DOEFF_RESULT_SESSION_ID": self.session_id,
            "DOEFF_AGENTD_SOCKET": str(self.harness.socket_path),
            "DOEFF_AGENTD_BIN": str(self.harness.agentd_bin),
            **(extra_env or {}),
        }
        binding = binding_from_session_env(agent_type, session_env)
        self.harness.client.launch_session(
            session_id=self.session_id,
            session_name=self.session_id,
            agent_type=agent_type,
            work_dir=self.work_dir,
            command=command,
            prompt=prompt,
            lifecycle=lifecycle,
            binding=binding,
            session_env=session_env,
            expected_result=expected_result,
        )

    def launch_m1(
        self,
        *,
        agent_type: str,
        prompt: str,
        expected_result: dict[str, Any] | None = None,
        extra_env: dict[str, str] | None = None,
        resume_script: list[dict[str, Any]] | None = None,
    ) -> None:
        """M1 (PATH shadowing): install the conformance agent as `codex` and
        `claude` on a scenario-owned PATH dir and launch WITHOUT `command=`,
        so agentd's REAL launch pipeline runs end to end:
        resolve_launch_command → build_codex_argv/build_claude_argv (incl.
        the doeff_result ResultChannel wiring) → wait_for_repl_idle → prompt
        paste (main.rs:1322/1345/1405/1791).

        CONFORMANCE_SCRIPT/JOURNAL are baked into the shim (not session_env)
        so the journal keeps working even if the pane shell rewrites its
        environment; the DOEFF_RESULT_* triple still travels via session_env
        because report_result / await_monitor_ack steps need it at runtime.
        The shim must render the idle frame promptly — wait_for_repl_idle
        blocks the launch RPC until the REPL glyph appears (or 120s).

        ZDOTDIR: a bare `session_env["PATH"]` prepend is NOT enough. The
        pane runs the user's login zsh, and its startup files rebuild PATH
        after tmux's `-e` injection — macOS /etc/zprofile (path_helper)
        demotes inherited entries and a typical ~/.zprofile (`brew
        shellenv`) re-prepends the dirs holding the REAL codex/claude
        (observed live on this machine: the probe pane resolved the real
        codex despite the shim being first in the injected PATH). Pointing
        ZDOTDIR at a scenario-owned dir whose .zshenv/.zprofile/.zshrc do
        exactly one thing (re-prepend the shim dir) makes resolution
        deterministic and keeps operator rc files out of the suite.
        """
        from doeff_agents.adapters.base import AgentSessionLifecycle

        shim_dir = self.harness.runtime_dir / f"shim-{self.session_id}"
        shim_dir.mkdir(parents=True, exist_ok=True)
        # S21: a revived incarnation (resume/fork argv) plays a different act
        # of the scenario — bake the alternate script into the shim so the
        # daemon's resume pipeline (which restores the persisted overlay, not
        # per-launch test env) still reaches it.
        resume_export = ""
        if resume_script is not None:
            resume_path = (
                self.harness.runtime_dir / f"script-resume-{self.session_id}.json"
            )
            resume_path.write_text(json.dumps(resume_script), encoding="utf-8")
            resume_export = (
                f"export CONFORMANCE_RESUME_SCRIPT={shlex.quote(str(resume_path))}\n"
            )
        for name in ("codex", "claude"):
            # CONFORMANCE_KIND: the shim exec()s the python agent, so
            # argv[0] is the agent script — the shim's own name is the only
            # place the kind survives (S21 conversation contract needs it).
            shim_body = (
                "#!/bin/sh\n"
                f"export CONFORMANCE_SCRIPT={shlex.quote(str(self.script_path))}\n"
                f"export CONFORMANCE_JOURNAL={shlex.quote(str(self.journal_path))}\n"
                f"export CONFORMANCE_KIND={name}\n"
                f"{resume_export}"
                f'exec {shlex.quote(sys.executable)} {shlex.quote(str(AGENT_SCRIPT))} "$@"\n'
            )
            shim = shim_dir / name
            shim.write_text(shim_body, encoding="utf-8")
            shim.chmod(0o755)
        zdotdir = self.harness.runtime_dir / f"zdot-{self.session_id}"
        zdotdir.mkdir(parents=True, exist_ok=True)
        prepend = f'export PATH={shlex.quote(str(shim_dir))}:"$PATH"\n'
        for rc_name in (".zshenv", ".zprofile", ".zshrc"):
            (zdotdir / rc_name).write_text(prepend, encoding="utf-8")
        session_env = {
            "PATH": f"{shim_dir}:{os.environ['PATH']}",
            "ZDOTDIR": str(zdotdir),
            "DOEFF_RESULT_SESSION_ID": self.session_id,
            "DOEFF_AGENTD_SOCKET": str(self.harness.socket_path),
            "DOEFF_AGENTD_BIN": str(self.harness.agentd_bin),
            **(extra_env or {}),
        }
        binding = binding_from_session_env(agent_type, session_env)
        self.harness.client.launch_session(
            session_id=self.session_id,
            session_name=self.session_id,
            agent_type=agent_type,
            work_dir=self.work_dir,
            prompt=prompt,
            lifecycle=AgentSessionLifecycle.RUN_TO_COMPLETION,
            binding=binding,
            session_env=session_env,
            expected_result=expected_result,
        )

    def journal(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
