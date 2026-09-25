import importlib.util
import os
import resource
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Machine premises: a test that needs a tool this machine may not have
# (agora-redesign #639, 2026-09-26).
#
# A tool on PATH is not a working tool.  The daily run's pod has the router's
# entry point for `codex` on PATH but no binary behind it, so `shutil.which`
# said "present" and the real-codex test went red (exit 127) for a fact about
# the machine, not about the code.  `machine_tool` starts the tool once and
# skips the test when it does not start.
#
# A skip alone is silent: the run would read "measured, nothing red" for a
# test that never ran.  So at the end of the session the unmet premises are
# named, on one line, as "not executed" in the check layer's own format (dotfiles
# agentcli remote_check — the file the land tool reads; it records the line as
# missing coverage, not as a red).  The format is read from the check layer on
# every run and never copied here: a copy keeps agreeing with itself on the
# day the layer changes its spelling.  On a machine with only a clone of this
# repository there is no check layer, and the skip reasons are all that is
# printed.
#
# Only "does the tool start" is a premise.  A tool that starts and then fails
# (a wrong answer, a missing login) is the test's own red.
# ---------------------------------------------------------------------------
_CHECK_LAYER = Path.home() / "dotfiles" / "agentcli" / "src" / "agentcli" / "remote_check.py"
_CHECK_LAYER_MODULE = "doeff_check_layer_remote_check"
_PREMISE_UNMET = "machine premise unmet:"
#: The check layer's word for "the command's executable cannot be started here".
_PREMISE_KIND = "tool-absent"
_PROBE_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class _ToolStarts:
    """The probe answered with exit 0: the test may use the tool at ``path``."""

    path: str


@dataclass(frozen=True)
class _ToolDoesNotStart:
    """The probe could not start the tool: ``detail`` becomes the skip reason."""

    detail: str


def _probe_tool(name: str, probe: Sequence[str]) -> _ToolStarts | _ToolDoesNotStart:
    """Start ``name`` once with ``probe`` and read whether it answered with exit 0."""
    path = shutil.which(name)
    if path is None:
        return _ToolDoesNotStart(f"{name} is not on PATH")
    asked = " ".join(probe)
    try:
        answer = subprocess.run(
            [path, *probe],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ToolDoesNotStart(
            f"{name} ({path}) did not answer `{asked}` within {_PROBE_TIMEOUT_S:.0f}s"
        )
    except OSError as error:
        return _ToolDoesNotStart(f"{name} ({path}) cannot be started: {error}")
    if answer.returncode != 0:
        # The last line is the tool's final word (a router's "no binary behind it").
        said = (answer.stderr.strip() or answer.stdout.strip()).splitlines()
        last_line = said[-1] if said else "no output"
        return _ToolDoesNotStart(f"{name} ({path}) exits {answer.returncode}: {last_line}")
    return _ToolStarts(path)


@pytest.fixture(scope="session")
def machine_tool() -> Callable[..., str]:
    """``machine_tool("codex")`` → the path of a tool that starts on this machine, or skip.

    Extra arguments replace the probe (default ``--version``).  Each tool is
    probed once per session.
    """
    answers: dict[tuple[str, ...], _ToolStarts | _ToolDoesNotStart] = {}

    def require(name: str, *probe: str) -> str:
        """Give the test a tool that starts, or skip it as an unmet machine premise."""
        asked = probe or ("--version",)
        key = (name, *asked)
        if key not in answers:
            answers[key] = _probe_tool(name, asked)
        match answers[key]:
            case _ToolStarts(path=path):
                return path
            case _ToolDoesNotStart(detail=detail):
                pytest.skip(f"{_PREMISE_UNMET} {detail}")

    return require


def _check_layer():
    """Load the check layer's vocabulary, or None when this machine has none."""
    module = sys.modules.get(_CHECK_LAYER_MODULE)
    if module is not None:
        return module
    if not _CHECK_LAYER.exists():
        return None
    spec = importlib.util.spec_from_file_location(_CHECK_LAYER_MODULE, _CHECK_LAYER)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_CHECK_LAYER_MODULE] = module  # dataclass reads sys.modules
    spec.loader.exec_module(module)
    return module


def _unmet_premises(skipped: Sequence[pytest.TestReport]) -> list[tuple[str, str]]:
    """(nodeid, detail) of every test skipped because a machine premise was unmet."""
    unmet: list[tuple[str, str]] = []
    for report in skipped:
        longrepr = report.longrepr
        if not (isinstance(longrepr, tuple) and len(longrepr) == 3):
            continue
        reason = str(longrepr[2]).removeprefix("Skipped: ")
        if reason.startswith(_PREMISE_UNMET):
            unmet.append((report.nodeid, reason.removeprefix(_PREMISE_UNMET).strip()))
    return unmet


def pytest_terminal_summary(terminalreporter):
    """Name the unmet machine premises as not executed, on one line (check layer format)."""
    unmet = _unmet_premises(terminalreporter.stats.get("skipped", []))
    if not unmet:
        return
    named = "; ".join(f"{nodeid}: {detail}" for nodeid, detail in unmet)
    terminalreporter.ensure_newline()
    layer = _check_layer()
    if layer is not None and _PREMISE_KIND in layer.UNEXECUTED_KINDS:
        terminalreporter.write(layer.unexecuted_line([_PREMISE_KIND], named))
        return
    if layer is None:
        where = "no check layer here"
    else:
        where = f"the check layer at {_CHECK_LAYER} does not know the kind {_PREMISE_KIND}"
    terminalreporter.write_line(
        f"{len(unmet)} test(s) skipped for a machine premise ({where}): {named}"
    )


# ---------------------------------------------------------------------------
# Broad-test-run admission (agora toolchain, ADR-DOTFILES-027
# "broad-test-runs-need-an-explicit-grant", 2026-09-15 / stage 11 lane 11k).
#
# 広範囲な選択の走行(この repo の全数 = 1,500 本超)は、頭脳が発行した期限つきの許可を
# 1 回消費できた時だけ本体を走らせる。判定(広範囲か)・30 分の窓の合算・許可の消費は
# **正本 1 点**(agora の道具立て ~/dotfiles/agent/tests/broad_run_admission.py)が持ち、
# この repo は .agents/land-queue.toml の [test-admission] で**値だけ**を宣言する
# (判定の写しをここへ置かない — 写すと方策を動かした日に片方だけ古い答えを返す)。
#
# The admission only binds where the agora toolchain is installed.  On a machine
# that just has a clone of this repository there is no grant office and no
# brain to ask, so the hook prints one line and lets the run through: gating it
# there would mean no test could ever run.  Where the toolchain *is* present the
# hook is fail-closed (an unreadable policy or an unobtainable grant stops the
# session before a single test body runs).
_ADMISSION_CANON = Path.home() / "dotfiles" / "agent" / "tests" / "broad_run_admission.py"
#: 固定の module 名 — 同じ走行の中で道具立て側の結線と同じ個体を見る(窓と印を共有する)。
_ADMISSION_MODULE = "ai_broad_run_admission"


def _broad_run_admission():
    """Load the canonical admission implementation, or None when absent."""
    module = sys.modules.get(_ADMISSION_MODULE)
    if module is not None:
        return module
    if not _ADMISSION_CANON.exists():
        return None
    spec = importlib.util.spec_from_file_location(_ADMISSION_MODULE, _ADMISSION_CANON)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ADMISSION_MODULE] = module  # dataclass reads sys.modules
    spec.loader.exec_module(module)
    return module


def pytest_collection_finish(session):
    """収集の後・本体の前の受付(広範囲なら許可を 1 回消費できた時だけ走らせる)。"""
    admission = _broad_run_admission()
    if admission is None:
        sys.stderr.write(
            f"test-admission: 受付の正本({_ADMISSION_CANON})がこの宿に無いので素通し"
            " — 広範囲の走行の許可は求めない\n"
        )
        return
    admission.collection_finish(session)


# ---------------------------------------------------------------------------
# One bytecode setting for every test run: no writes, standard location.
#
# The land tool starts every gate and the daily full run with
# PYTHONDONTWRITEBYTECODE=1 (so the tree under test gets no __pycache__), and a
# developer's run usually does not.  A test whose answer depends on bytecode
# being written was therefore green on the author's machine and red only in the
# daily run (measured 2026-09-24: the two @effectful cache tests of 46dcbfc7).
# Pinning the setting here makes every run give the same answer: a test that
# needs bytecode writes must say so itself
# (``monkeypatch.setattr(sys, "dont_write_bytecode", False)``).
#
# Python has exactly two settings for bytecode files — whether they are written
# (sys.dont_write_bytecode / PYTHONDONTWRITEBYTECODE) and where they go
# (sys.pycache_prefix / PYTHONPYCACHEPREFIX) — and both are pinned, so neither a
# machine's environment nor a later change that honours the prefix can split
# the answer again.
#
# Session scope, so module- and session-scoped fixtures run under it too.  It
# starts after collection, so collecting (and pytest's assertion rewrite) still
# writes and reuses the bytecode cache on a developer's machine.  The env vars
# are set as well, so subprocesses started by tests inherit them.  Package
# suites are covered too: `make test-packages` runs `pytest packages/<p>/tests`
# from the repo root, whose pyproject.toml makes it the rootdir, so this
# conftest is loaded for them as well.
#
# A fixture that changes a setting directly and never restores it turns the pin
# off for everything after it — and hid both cache failures above in a run that
# included packages/doeff-hy/tests (measured 2026-09-24).  So the run fails
# when the settings are not back at the end.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _bytecode_settings_pinned():
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "dont_write_bytecode", True)
        patch.setattr(sys, "pycache_prefix", None)
        patch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        patch.delenv("PYTHONPYCACHEPREFIX", raising=False)
        yield
        at_the_end = (sys.dont_write_bytecode, sys.pycache_prefix)
    assert at_the_end == (True, None), (
        f"a test or fixture changed the bytecode settings and did not restore them "
        f"(dont_write_bytecode, pycache_prefix) = {at_the_end} — change them with "
        f"monkeypatch (or pytest.MonkeyPatch.context() in a wider fixture)"
    )
