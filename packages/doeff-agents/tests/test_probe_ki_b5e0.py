"""設計 ki-b5e0d04de958 の probe(push しない): 実 binary の器に SIGTERM を送り、行の語と宣言の file の在否を見る。

- 宣言あり(drain file が在る): 行の cause = host_drained・file は器の停止の前後で変わらない
- 宣言なし(file が無い・env は path を名指す): 行の cause = cancelled・**file は停止の後も無い**(器は宣言を作らない)
- env なし(join を通らない起動): 行の cause = cancelled
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from sessionhost_bin import resolve_sessionhost_bin
from test_sessionhost_headless import (
    STUBS,
    _launch_params,
    _obj,
    _pid_alive,
    _stored_row,
    _wait_real_host,
    _wait_until,
)

ENV_DRAIN_FILE = "DOEFF_SESSIONHOST_DRAIN_FILE"


def _spawn(root: Path, drain_env: str | None) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PATH"] = f"{STUBS}{os.pathsep}{env.get('PATH', '')}"
    env["DOEFF_SESSIONHOST_HEADLESS_DIR"] = str(root / "events")
    env["DOEFF_HEADLESS_STUB_DELAY"] = "30"
    env["XDG_STATE_HOME"] = str(root / "state")
    env.pop("DOEFF_AGENTD_ACP", None)
    env.pop(ENV_DRAIN_FILE, None)
    if drain_env is not None:
        env[ENV_DRAIN_FILE] = drain_env
    with (root / "host.log").open("w", encoding="utf-8") as log:
        return subprocess.Popen(
            [
                str(resolve_sessionhost_bin()),
                "--db", str(root / "agentd.sqlite"), "--socket", str(root / "agentd.sock"),
                "--prompt-judge-cmd", "", "--backend", "headless", "serve",
            ],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
        )


def _cut_one_turn(declare: bool, pass_env: bool) -> tuple[dict[str, object], bool, bool, str]:
    from doeff_agents.agentd_client import AgentdClient

    root = Path(tempfile.mkdtemp(prefix="doeff-probe-b5e0-"))
    drain = root / "drain"
    try:
        if declare:
            drain.write_text("pool-prestop probe-pod uid-0 2026-09-23T00:00:00Z pod termination\n", encoding="utf-8")
        before = drain.exists()
        proc = _spawn(root, str(drain) if pass_env else None)
        try:
            _wait_real_host(proc, root)
            client = AgentdClient(root / "agentd.sock", timeout=2.0)
            launched = client.request("session.launch", _launch_params(root, "h-probe", "claude"))
            assert isinstance(launched, dict)
            child_pid = _obj(launched, "backend_ref")["pid"]
            assert isinstance(child_pid, int)
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30.0)
            _wait_until(lambda: not _pid_alive(child_pid))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5.0)
        status, _awaiting, cause = _stored_row(root, "h-probe")
        assert status == "stopped"
        return cause, before, drain.exists(), (root / "host.log").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_probe_declared_stop_writes_host_drained() -> None:
    cause, before, after, _log = _cut_one_turn(declare=True, pass_env=True)
    assert cause["category"] == "host_drained"
    assert before and after


def test_probe_undeclared_stop_stays_cancelled_and_the_host_writes_no_declaration() -> None:
    cause, before, after, log = _cut_one_turn(declare=False, pass_env=True)
    assert not before
    assert not after, f"the host created the declaration itself:\n{log}"
    assert cause["category"] == "cancelled"


def test_probe_no_env_stays_cancelled() -> None:
    cause, _before, _after, _log = _cut_one_turn(declare=False, pass_env=False)
    assert cause["category"] == "cancelled"
