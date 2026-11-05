"""Reading the doeff-hy-check places (doeff_hy._env_places) must not load the process-wide handler module doeff_core_effects._os_process.

env_places only reads two environment variables ($DOEFF_HY_CHECK_CACHE and $XDG_CACHE_HOME — now also $DOEFF_HY_CHECK_PYRIGHT_THREADS, the pyright thread count),
yet as a handler answering ReadEnvironment it
loaded os_process's subprocess_handler (a large Hy module holding all the child-process, thread and signal handlers), and the Hy bytecode
check ran on every doeff-hy-check child process (1.8-1.9 s when warm, 4.7 s right after cutting a worktree — one type check
starts 4 to 8 child processes). The read is checked in a fresh Python process (so modules loaded by an earlier check in the same process are not counted).
"""

import subprocess
import sys

import pytest

PROBE: str = (
    "import sys\n"
    "from doeff_hy import _env_places as env_places\n"
    "print(env_places.check_cache_setting())\n"
    "print(env_places.cache_home())\n"
    "print(env_places.pyright_threads_setting())\n"
    "print('doeff_core_effects._os_process' in sys.modules)\n"
)


def probe() -> list[str]:
    """Output lines from running PROBE in a child Python (inheriting this process's environment — the Hy bytecode location etc. match the parent's)."""
    done = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, timeout=120)
    return done.stdout.strip().splitlines()


def test_reading_the_cache_places_does_not_load_the_process_handlers() -> None:
    lines = probe()
    assert lines[-1] == "False", lines


def test_the_cache_places_still_follow_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOEFF_HY_CHECK_CACHE", "/kept/check")
    monkeypatch.setenv("XDG_CACHE_HOME", "/kept/xdg")
    named = probe()
    assert named[:2] == ["/kept/check", "/kept/xdg"], named
    monkeypatch.setenv("DOEFF_HY_CHECK_CACHE", "")
    unset = probe()
    assert unset[0] == "None", unset


def test_the_pyright_threads_setting_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raw value of the pyright thread count is also received from this module, not read from os.environ directly by the doeff-hy-check entry point (same as the upper bound on
    expansion child processes). Judging the value (an integer >= 1) is up to the caller, pyright_threads."""
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "2")
    named = probe()
    assert named[2] == "2", named
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "")
    unset = probe()
    assert unset[2] == "None", unset
