import os
import resource
import signal
import sys
import threading
from contextlib import suppress

import pytest

# ---------------------------------------------------------------------------
# Load-scaled deadlines (ADR-DOE-ENFORCE-001 R6)
#
# The two deadlines below (pytest-timeout per test, and the SIGKILL watchdog)
# are wall-clock, but the thing they exist to catch — a HANG — is not.  On a
# machine that is oversubscribed N-fold, every test that waits on an external
# process (semgrep, a CLI subprocess) takes N times longer while the deadline
# stays constant, so the deadline stops measuring "hung" and starts measuring
# "busy" — and the gate turns red on work that is perfectly correct.
#
# Measured 2026-08-17: the land gate failed 5 times in a row on this machine
# (18 cores, 1-minute load average 60-156 sustained) — each time on a
# different subprocess-spawning test, and the same failure reproduces on
# pristine main, so it is a property of the deadline, not of any branch.
# `semgrep` alone took 29.7 s at load 80 against a 60 s budget; under the full
# battery it crosses.  Nine land attempts across two seats died this way.
#
# The fix is to make the deadline a function of oversubscription instead of a
# constant.  A genuine hang is still bounded: the factor is capped, so the
# worst case is CAP x the base deadline, not unbounded.
#
# BOTH deadlines scale together, and the watchdog stays strictly above the
# per-test deadline.  Scaling only one is worse than scaling neither: raising
# pytest-timeout alone moves the failure into the watchdog, which does not
# fail the test — it SIGKILLs the whole process and the entire run's results
# are lost (observed 2026-08-17 while diagnosing this: PYTEST_TIMEOUT=600
# turned a red test into a dead process at 45% of the battery).
#
#   PYTEST_DEADLINE_SCALE=off   disable scaling (CI, where load is controlled)
#   PYTEST_DEADLINE_SCALE_CAP   max factor (default 8)
# ---------------------------------------------------------------------------
_DEADLINE_SCALE_CAP = max(1.0, float(os.environ.get("PYTEST_DEADLINE_SCALE_CAP", "8")))


def _oversubscription() -> float:
    """1-minute load average per core; 1.0 on an idle or unmeasurable machine."""
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        return 1.0
    return max(1.0, load1 / (os.cpu_count() or 1))


def deadline_scale() -> float:
    """The factor every wall-clock test deadline is multiplied by."""
    if os.environ.get("PYTEST_DEADLINE_SCALE", "").strip().lower() == "off":
        return 1.0
    return min(_oversubscription(), _DEADLINE_SCALE_CAP)


def scaled_watchdog_timeout(base: float, per_test_timeout: float, scale: float) -> int:
    """The watchdog deadline: scaled, and always clear of the per-test deadline.

    The watchdog is the last resort for code stuck inside a C extension, where
    pytest-timeout cannot interrupt.  It must therefore fire strictly AFTER
    pytest-timeout has had its chance — otherwise a merely-slow test is
    answered with SIGKILL (whole run lost) instead of one red test.
    """
    return int(max(base * scale, per_test_timeout * scale + 30.0))

# ---------------------------------------------------------------------------
# Memory guard: limit to 32GB to prevent OOM-killing tmux/codex
# ---------------------------------------------------------------------------
_MAX_RSS_BYTES = 32 * 1024 * 1024 * 1024  # 32GB
with suppress(OSError, ValueError):
    resource.setrlimit(resource.RLIMIT_AS, (_MAX_RSS_BYTES, _MAX_RSS_BYTES))

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
_WATCHDOG_BASE = float(os.environ.get("PYTEST_WATCHDOG_TIMEOUT", "90"))
_DEADLINE_SCALE = deadline_scale()
# Provisional until pytest_configure reads the real per-test deadline out of
# the ini — there is exactly one home for that number and it is pyproject.toml.
_WATCHDOG_TIMEOUT = int(_WATCHDOG_BASE * _DEADLINE_SCALE)
_watchdog_timer: threading.Timer | None = None


def _watchdog_kill(timeout: int):
    """Last resort: kill the process if a test hangs beyond all timeouts."""
    print(
        f"\n\nWATCHDOG: Test hung for {timeout}s beyond all timeouts. "
        f"Killing process with SIGKILL.\n",
        file=sys.stderr,
        flush=True,
    )
    os.kill(os.getpid(), signal.SIGKILL)


def _reset_watchdog(timeout: int | None = None):
    global _watchdog_timer  # noqa: PLW0603
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
    active_timeout = timeout or _WATCHDOG_TIMEOUT
    _watchdog_timer = threading.Timer(active_timeout, _watchdog_kill, args=(active_timeout,))
    _watchdog_timer.daemon = True
    _watchdog_timer.start()


def _stop_watchdog():
    global _watchdog_timer  # noqa: PLW0603
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
        _watchdog_timer = None


def pytest_configure(config):
    """Scale both wall-clock deadlines by the machine's oversubscription.

    The per-test deadline has exactly one home — ``[tool.pytest.ini_options]
    timeout`` — so it is read from there rather than mirrored here, and the
    watchdog is derived from the scaled value so the two can never cross.
    """
    global _WATCHDOG_TIMEOUT  # noqa: PLW0603

    per_test_base = _per_test_base_seconds(config)
    scaled_per_test = per_test_base * _DEADLINE_SCALE
    if per_test_base > 0:
        config.option.timeout = scaled_per_test
    _WATCHDOG_TIMEOUT = scaled_watchdog_timeout(
        _WATCHDOG_BASE, per_test_base, _DEADLINE_SCALE
    )

    if _DEADLINE_SCALE > 1.0:
        # Never scale silently: a stretched deadline is also the signal that
        # this machine is oversubscribed, and a run that took 8x longer than
        # usual must say so rather than just come back green.
        print(
            f"\ndeadline scale x{_DEADLINE_SCALE:.1f} "
            f"(load/core) — per test {scaled_per_test:.0f}s, "
            f"watchdog {_WATCHDOG_TIMEOUT}s\n",
            file=sys.stderr,
            flush=True,
        )


def _per_test_base_seconds(config) -> float:
    """The unscaled per-test deadline, in pytest-timeout's own precedence.

    Explicit beats declared: an env var or ``--timeout`` from the caller is
    the number they meant, and scaling theirs is right — silently replacing
    it with the ini default would make the escape hatch a lie.  0 = not set.
    """
    readers = (
        lambda: os.environ.get("PYTEST_TIMEOUT"),
        lambda: config.option.timeout,
        lambda: config.getini("timeout"),
    )
    for read in readers:
        with suppress(Exception):
            value = read()
            if value not in (None, ""):
                return float(value)
    return 0.0


def pytest_collection_modifyitems(config, items):
    """Scale per-test ``@pytest.mark.timeout(...)`` deadlines too.

    pytest-timeout gives a marker precedence over the ini, so scaling the ini
    alone would leave exactly the tests that declared themselves slow — the
    ones most likely to spawn a subprocess — on an unscaled deadline.
    """
    if _DEADLINE_SCALE <= 1.0:
        return
    for item in items:
        marker = item.get_closest_marker("timeout")
        if marker is None or not marker.args:
            continue
        with suppress(TypeError, ValueError):
            item.add_marker(pytest.mark.timeout(float(marker.args[0]) * _DEADLINE_SCALE))


def pytest_runtest_setup(item):
    """Reset watchdog at the start of each test."""
    _reset_watchdog(_watchdog_timeout_for_item(item))


def pytest_runtest_teardown(item, nextitem):
    """Reset watchdog after each test (covers slow teardown)."""
    _reset_watchdog()


def pytest_sessionfinish(session, exitstatus):
    """Stop watchdog when pytest finishes."""
    _stop_watchdog()


def _watchdog_timeout_for_item(item) -> int:
    marker = item.get_closest_marker("timeout")
    if marker is None or not marker.args:
        return _WATCHDOG_TIMEOUT
    try:
        timeout = float(marker.args[0])
    except (TypeError, ValueError):
        return _WATCHDOG_TIMEOUT
    # The marker was already scaled at collection time, so this only has to
    # keep the watchdog clear of it.
    return int(max(_WATCHDOG_TIMEOUT, timeout + 30.0))
