import os
import resource
import signal
import sys
import threading

# ---------------------------------------------------------------------------
# Memory guard: limit to 32GB to prevent OOM-killing tmux/codex
# ---------------------------------------------------------------------------
_MAX_RSS_BYTES = 32 * 1024 * 1024 * 1024  # 32GB
try:
    resource.setrlimit(resource.RLIMIT_AS, (_MAX_RSS_BYTES, _MAX_RSS_BYTES))
except (ValueError, resource.error):
    pass  # Some systems don't support RLIMIT_AS

# ---------------------------------------------------------------------------
# Hard watchdog: kill the process if a single test hangs beyond timeout.
#
# pytest-timeout uses signal or thread method, but neither can reliably
# interrupt code stuck inside C extensions (like the Rust VM). This watchdog
# is the last resort — it kills the entire process with SIGKILL.
#
# The watchdog resets at the start of each test (via the pytest hook).
# If no test starts within WATCHDOG_TIMEOUT seconds, the process dies.
# ---------------------------------------------------------------------------
_WATCHDOG_TIMEOUT = int(os.environ.get("PYTEST_WATCHDOG_TIMEOUT", "90"))
_watchdog_timer: threading.Timer | None = None


def _watchdog_kill():
    """Last resort: kill the process if a test hangs beyond all timeouts."""
    print(
        f"\n\nWATCHDOG: Test hung for {_WATCHDOG_TIMEOUT}s beyond all timeouts. "
        f"Killing process with SIGKILL.\n",
        file=sys.stderr,
        flush=True,
    )
    os.kill(os.getpid(), signal.SIGKILL)


def _reset_watchdog():
    global _watchdog_timer
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
    _watchdog_timer = threading.Timer(_WATCHDOG_TIMEOUT, _watchdog_kill)
    _watchdog_timer.daemon = True
    _watchdog_timer.start()


def _stop_watchdog():
    global _watchdog_timer
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
        _watchdog_timer = None


def pytest_runtest_setup(item):
    """Reset watchdog at the start of each test."""
    _reset_watchdog()


def pytest_runtest_teardown(item, nextitem):
    """Reset watchdog after each test (covers slow teardown)."""
    _reset_watchdog()


def pytest_sessionfinish(session, exitstatus):
    """Stop watchdog when pytest finishes."""
    _stop_watchdog()
