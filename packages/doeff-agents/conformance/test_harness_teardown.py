"""Harness teardown — the daemon a harness run spawned is gone afterwards.

agora-redesign #3026: conformance runs left `doeff-sessionhost ... serve`
daemons behind for hours (ppid = the user systemd, --db under
/tmp/agentd-conf-*). The harness stops the daemon's whole process group where
it spawned it: on a passing scenario, on a failing one, and when the daemon
never became ready (`__enter__` raises, so `__exit__` never runs). The spawner
dying mid-startup is the daemon's own boundary — S28e in
test_s28_orphaned_host_boundary.py.
"""

import os
import signal
import time
from pathlib import Path

import pytest
from harness import AgentdHarness


def _alive(pid: int) -> bool:
    """The spawned daemon itself, or any member of the group it leads."""
    for probe in (os.kill, os.killpg):
        try:
            probe(pid, 0)
        except ProcessLookupError:
            continue
        return True
    return False


def _force_stop(pid: int) -> None:
    for kill in (os.killpg, os.kill):
        try:
            kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue


def _assert_gone(pid: int, budget_s: float = 5.0) -> None:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.1)
    _force_stop(pid)  # never leave the leak under test behind
    raise AssertionError(f"the spawned daemon (pid {pid}) or its group outlived the harness")


def test_a_passing_scenario_leaves_no_daemon() -> None:
    harness = AgentdHarness()
    with harness:
        assert os.getpgid(harness.daemon_pid) == harness.daemon_pid, (
            "the daemon must lead its own process group"
        )
        harness.client.status()
    _assert_gone(harness.daemon_pid)


def test_a_failing_scenario_leaves_no_daemon() -> None:
    harness = AgentdHarness()
    with pytest.raises(RuntimeError, match="scenario failed"), harness:
        raise RuntimeError("scenario failed")
    _assert_gone(harness.daemon_pid)


def test_a_daemon_that_never_gets_ready_is_stopped_before_the_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`__enter__` raises after the readiness budget (15s); `__exit__` never
    runs, so the spawn site itself must stop what it spawned."""
    fake = tmp_path / "never-ready-agentd"
    fake.write_text("#!/bin/sh\nexec sleep 300\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CONFORMANCE_AGENTD_BIN", str(fake))
    harness = AgentdHarness()
    with pytest.raises(AssertionError, match="not ready"), harness:
        pass
    _assert_gone(harness.daemon_pid)
