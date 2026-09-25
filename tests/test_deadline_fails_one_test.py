"""A test over its deadline is one red test; only the watchdog ends a run, and it names the test.

pyproject.toml runs pytest-timeout with the signal method, so a test over its deadline fails
alone and the tests after it still run.  The thread method ended the whole process instead:
on 2026-09-25 and 09-26 the daily run of the doeff-agents suite stopped at about 6% on one
slow test, and the other 94% was never measured (agora-redesign#639).

A signal cannot reach code stuck inside a C extension, so the root conftest's watchdog is
the last resort.  When it fires it writes pytest's own short-summary line naming the test it
was watching and ends the process with exit status 1 — the land tool reads failed-test names
from those lines, and reads a process ended by a signal (the SIGKILL the watchdog used
before) as an outside kill rather than a red run.

Each test below runs a probe as its own pytest session under the root conftest and the root
pytest settings (tests/_root_conftest_runs.py), so what is checked is the repository's own
deadline setup.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import tomllib

from tests._root_conftest_runs import REPO_ROOT, run_under_root_conftest

# The inner watchdog is at least 30 s above the inner per-test deadline
# (scaled_watchdog_timeout in the root conftest, ADR-DOE-ENFORCE-001 R6), so the
# watchdog case needs a little over 31 s by construction.
_INNER_RUN_BUDGET = 100.0

SLOW_TEST_THEN_ANOTHER = """
import time


def test_slow():
    time.sleep(30)


def test_after_the_slow_one():
    pass
"""

HANG_THAT_NO_SIGNAL_REACHES = """
import signal
import time
from pathlib import Path


def test_before():
    Path("ran-before").touch()


def test_blocks_signals_and_hangs():
    # Stand-in for a hang inside a C extension: SIGALRM never reaches the test.
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    time.sleep(600)


def test_after():
    Path("ran-after").touch()
"""


def _run_inner_pytest(directory: Path, probe_source: str) -> subprocess.CompletedProcess[str]:
    """Run a probe with a 1 s per-test deadline, so its watchdog fires 31 s into a hang."""
    return run_under_root_conftest(
        directory,
        probe_source,
        pytest_args=["--timeout=1"],
        env={"PYTEST_DEADLINE_SCALE": "off", "PYTEST_WATCHDOG_TIMEOUT": "1"},
        budget=_INNER_RUN_BUDGET,
    )


def test_the_per_test_deadline_uses_the_signal_method() -> None:
    settings = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    method = settings["tool"]["pytest"]["ini_options"]["timeout_method"]
    assert method == "signal", (
        f"timeout_method = {method!r}: the thread method ends the whole process on the first "
        f"slow test, so every test after it goes unmeasured (agora-redesign#639)"
    )


@pytest.mark.timeout(_INNER_RUN_BUDGET + 20)
def test_a_test_over_its_deadline_fails_alone_and_the_run_goes_on(tmp_path: Path) -> None:
    result = _run_inner_pytest(tmp_path, SLOW_TEST_THEN_ANOTHER)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "Timeout (>1.0s) from pytest-timeout" in result.stdout, result.stdout
    assert "FAILED test_probe.py::test_slow" in result.stdout, result.stdout
    assert "1 failed, 1 passed" in result.stdout, result.stdout


@pytest.mark.timeout(_INNER_RUN_BUDGET + 20)
def test_the_watchdog_names_the_hung_test_and_ends_with_status_1(tmp_path: Path) -> None:
    result = _run_inner_pytest(tmp_path, HANG_THAT_NO_SIGNAL_REACHES)

    assert result.returncode == 1, (
        f"the watchdog ended the run with {result.returncode} — the land tool reads a signal "
        f"exit as an outside kill, not as a red run\n{result.stdout}{result.stderr}"
    )
    summary = result.stdout.split("short test summary info", 1)
    assert len(summary) == 2, f"no short test summary in the output:\n{result.stdout}"
    assert (
        "FAILED test_probe.py::test_blocks_signals_and_hangs - WATCHDOG: no progress for "
        in summary[1]
    ), result.stdout
    assert (tmp_path / "ran-before").exists()
    assert not (tmp_path / "ran-after").exists()
