import importlib.util
import os
import resource
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Collection, Generator, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

import pytest

# ---------------------------------------------------------------------------
# VM conformance oracle on (ADR-DOE-ENFORCE-001 R4・agora-redesign #980)
#
# Every doeff-vm build carries the per-step invariant checks; they run only
# when turned on. doeff's own test sessions turn them on here, so the oracle
# no longer depends on which path last built the extension (`make sync` with
# the old cargo feature vs `uv sync` without — the same venv ran 15x slower or
# faster). The switch is pytest's own ini value ``vm_invariant_checks``
# (default true — ``-o vm_invariant_checks=false`` turns it off, and then
# tests/test_vm_invariant_checks_enabled.py goes red, as it should), not an
# environment variable, which only a foundation handler reads (DOEFF004・
# agora-redesign #3012). Child processes do not inherit it: the one place
# tests start a child CLI (tests/cli/cli_child.with_settings) hands the child
# this session's state as DOEFF_VM_INVARIANT_CHECKS explicitly.
# ---------------------------------------------------------------------------
VM_INVARIANT_CHECKS_INI = "vm_invariant_checks"


def _turn_vm_oracle_on(enabled: bool) -> None:
    """Make every VM run in this test session check its invariants after each step (R4)."""
    from doeff_vm.doeff_vm import set_invariant_checks

    set_invariant_checks(enabled)


# On from the start, in case a plugin runs a program before pytest_configure
# reads the ini value (which can only turn it off).
_turn_vm_oracle_on(True)

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
# Oversubscription is the 1-minute load average over the CPUs THIS process may
# use — its affinity, cut by the CPU quota (cgroup v2 cpu.max) of its cgroup and
# every cgroup above it — not over the machine's cores.  A container limited to
# 8 CPUs on a 16-core machine still sees 16 in os.cpu_count() and its affinity,
# and dividing by 16 halved what it lived through.  Measured 2026-10-11 20:00
# JST (the daily run on atlas): the worker Pod sat at 7.3-8.0 of its 8 CPUs from
# 20:10 to 20:40, and a 30 s child of packages/doeff-adr/tests/
# test_indexed_collection.py was killed (card ki-bb91f171d222).  The load
# average still counts the whole machine, so load from outside the cgroup reads
# high — the safe side: a deadline held longer, bounded by the cap.
#
# BOTH deadlines scale together, and the watchdog stays strictly above the
# per-test deadline.  Scaling only one is worse than scaling neither: raising
# pytest-timeout alone moves the failure into the watchdog, which does not
# fail one test and go on — it ends the whole process and every test after
# the hung one goes unrun (observed 2026-08-17 while diagnosing this, when the
# watchdog still used SIGKILL: PYTEST_TIMEOUT=600 turned a red test into a
# dead process at 45% of the battery).
#
# The settings are pytest's own ini values (registered in pytest_addoption,
# overridden by whoever starts the run with ``-o <name>=<value>``) — not
# environment variables, which only a foundation handler reads (DOEFF004・
# agora-redesign #3012):
#
#   deadline_scale = off        disable scaling (CI, where load is controlled)
#   deadline_scale_cap = 8      max factor
#   watchdog_timeout = 90       the watchdog's unscaled base, in seconds
# ---------------------------------------------------------------------------
_DEADLINE_INI = (
    ("deadline_scale", "auto", "off = hold every wall-clock deadline fixed (ADR-DOE-ENFORCE-001 R6)"),
    ("deadline_scale_cap", "8", "the largest factor the load scaling may stretch a deadline by (R6)"),
    ("watchdog_timeout", "90", "the hard watchdog's unscaled base deadline, in seconds (R6)"),
)


def pytest_addoption(parser):
    """Register the deadline settings as ini values (``-o deadline_scale=off`` and the like)."""
    for name, default, help_text in _DEADLINE_INI:
        parser.addini(name, help_text, default=default)
    parser.addini(
        VM_INVARIANT_CHECKS_INI,
        "false = run the VM without its per-step invariant checks (ADR-DOE-ENFORCE-001 R4)",
        type="bool",
        default=True,
    )


# Where this process's cgroup (v2) is named, and where its CPU quota (cpu.max) is read.
_PROC_SELF_CGROUP = Path("/proc/self/cgroup")
_CGROUP_MOUNT = Path("/sys/fs/cgroup")


def _affinity_cpus() -> int:
    """The CPUs the scheduler lets this process run on (the machine's where there is no affinity call — macOS)."""
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is None:
        return os.cpu_count() or 1
    return len(affinity(0)) or 1


def _cpu_max(path: Path) -> float | None:
    """One cgroup's CPU quota in CPUs (``cpu.max`` = "<quota> <period>"); None = unlimited or no file."""
    try:
        quota, period = path.read_text().split()
    except FileNotFoundError:  # this cgroup has no cpu controller
        return None
    if quota == "max":
        return None
    return int(quota) / int(period)


def _cgroup_cpu_limit() -> float | None:
    """The tightest CPU quota on this process's cgroup v2 and the cgroups above it, in CPUs.

    None where there is none: no quota, cgroup v1 only, or no /proc (macOS).  The mount
    shows at least this process's own cgroup — in a container (a cgroup namespace) the
    mount itself is that cgroup and /proc/self/cgroup names it "/".
    """
    try:
        lines = _PROC_SELF_CGROUP.read_text().splitlines()
    except FileNotFoundError:  # no /proc (macOS)
        return None
    own = next((line.removeprefix("0::") for line in lines if line.startswith("0::")), None)
    if own is None:
        return None
    parts = PurePosixPath(own).parts[1:]
    quotas = (
        _cpu_max(_CGROUP_MOUNT.joinpath(*parts[:depth]) / "cpu.max")
        for depth in range(len(parts), -1, -1)
    )
    return min((quota for quota in quotas if quota is not None), default=None)


def usable_cpus() -> float:
    """The CPUs this process can use: its affinity, cut to the CPU quota of its cgroups."""
    affinity = float(_affinity_cpus())
    limit = _cgroup_cpu_limit()
    return affinity if limit is None else min(affinity, limit)


def _oversubscription() -> float:
    """1-minute load average per CPU this process may use; 1.0 on an idle or unmeasurable machine."""
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        return 1.0
    return max(1.0, load1 / usable_cpus())


def deadline_scale(setting: str, cap: float) -> float:
    """The factor every wall-clock test deadline is multiplied by (setting = the ``deadline_scale`` ini value)."""
    if setting.strip().lower() == "off":
        return 1.0
    return min(_oversubscription(), max(1.0, cap))


def scaled_watchdog_timeout(base: float, per_test_timeout: float, scale: float) -> int:
    """The watchdog deadline: scaled, and always clear of the per-test deadline.

    The watchdog is the last resort for code stuck inside a C extension, where
    pytest-timeout cannot interrupt.  It must therefore fire strictly AFTER
    pytest-timeout has had its chance — otherwise a merely-slow test is
    answered by ending the process (the rest of the run lost) instead of one
    red test.
    """
    return int(max(base * scale, per_test_timeout * scale + 30.0))

# ---------------------------------------------------------------------------
# Memory guard: limit to 32GB to prevent OOM-killing tmux/codex
# ---------------------------------------------------------------------------
_MAX_RSS_BYTES = 32 * 1024 * 1024 * 1024  # 32GB
with suppress(OSError, ValueError):
    resource.setrlimit(resource.RLIMIT_AS, (_MAX_RSS_BYTES, _MAX_RSS_BYTES))

# ---------------------------------------------------------------------------
# Hard watchdog: end the process if a single test hangs beyond all timeouts.
#
# pytest-timeout runs with the signal method (pyproject.toml): a test over its
# deadline fails as one test and the run goes on.  (The thread method ended the
# whole process instead — on 2026-09-25 and 09-26 the doeff-agents suite
# stopped at about 6% on one slow test and the rest went unmeasured.)  A signal
# cannot interrupt code stuck inside a C extension (like the Rust VM), so this
# watchdog is the last resort.
#
# When it fires it names the test it was watching, in the form of pytest's own
# short-summary line, and ends the process with exit status 1.  Both matter to
# the land tool: it reads the failed-test names from those lines, and it reads
# a process ended by a signal as "killed from outside", not as a red run — the
# SIGKILL this watchdog used before made a hang look like an outside kill with
# no test named.
#
# The watchdog resets at the start and at the teardown of each test (via the
# pytest hooks), so it measures one test's setup-call-teardown at a time.
# ---------------------------------------------------------------------------
# Provisional until pytest_configure reads the deadline ini values (pytest_addoption).
_WATCHDOG_BASE = 90.0
_DEADLINE_SCALE = 1.0
# Provisional until pytest_configure reads the real per-test deadline out of
# the ini — there is exactly one home for that number and it is pyproject.toml.
_WATCHDOG_TIMEOUT = int(_WATCHDOG_BASE * _DEADLINE_SCALE)
_watchdog_timer: threading.Timer | None = None


# The pytest plugin name under which the daily run's session writer (dotfiles
# agent/tests/session_answer.py) is registered.  It names each session with a
# token, and on a daily run the land tool reads a session's failed-test names
# only from its token lines — the bare FAILED line below looks the same as an
# inner pytest's line copied into a failed test's output (agora-redesign#645).
_SESSION_ANSWER_PLUGIN = "ai-session-answer"


def _watchdog_expired(config: pytest.Config, timeout: int, nodeid: str, phase: str) -> None:
    """Last resort: name the hung test and end the process with exit status 1."""
    line = (
        f"FAILED {nodeid}{phase} - WATCHDOG: no progress for {timeout}s beyond all timeouts; "
        f"the process was ended and the tests after it did not run"
    )
    try:
        # The hung test's output is being captured and would be lost with the
        # line; release the capture first (the same steps as pytest-timeout's
        # thread method).
        capman = config.pluginmanager.getplugin("capturemanager")
        if capman is not None:
            capman.suspend_global_capture(in_=True)
        # On a daily run, answer through the session writer: it names the hung
        # test with the session's token and writes the session's answer, so the
        # hang is read as this session's red rather than as a session that
        # vanished without answering.
        session = config.pluginmanager.get_plugin(_SESSION_ANSWER_PLUGIN)
        if session is not None:
            session.answer_stopped(nodeid)
        if config.pluginmanager.getplugin("terminalreporter") is None:
            print(line, flush=True)
        else:
            terminal = config.get_terminal_writer()
            terminal.line("")
            terminal.sep("=", "short test summary info")
            terminal.line(line)
            terminal.flush()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


def _reset_watchdog(
    config: pytest.Config, nodeid: str, timeout: int | None = None, phase: str = ""
) -> None:
    """Start watching one test phase, so a hang there is ended under that test's name."""
    global _watchdog_timer  # noqa: PLW0603
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
    active_timeout = timeout or _WATCHDOG_TIMEOUT
    _watchdog_timer = threading.Timer(
        active_timeout, _watchdog_expired, args=(config, active_timeout, nodeid, phase)
    )
    _watchdog_timer.daemon = True
    _watchdog_timer.start()


def _stop_watchdog():
    global _watchdog_timer  # noqa: PLW0603
    if _watchdog_timer is not None:
        _watchdog_timer.cancel()
        _watchdog_timer = None


# ---------------------------------------------------------------------------
# semgrep-core runs without io_uring.
#
# semgrep-core (OCaml 5 + Eio) opens one io_uring ring per domain on Linux, and
# the kernel charges ring memory to RLIMIT_MEMLOCK per uid — one budget shared by
# every process of that uid on the machine (8 MiB in the agent Pods).  When other
# semgrep runs of the same uid hold it, the next ring fails with ENOMEM at
# `io_uring_queue_init`, and semgrep reports "semgrep-core rule validation
# failed" with no finding (2026-10-16: a full root run went red on
# tests/semgrep/test_public_api_subpackage_imports_rule.py, green on the rerun).
# Eio's posix backend opens no ring; the full scan of .semgrep.yaml answers the
# same and takes about as long.  Set from pytest_configure — before collection,
# in every xdist worker, and for items without fixtures — so every semgrep a test
# starts inherits it; through MonkeyPatch, not os.environ (DOEFF004).  One patch
# per config, so an inner pytest run ending does not undo the outer run's.
# ---------------------------------------------------------------------------
_SEMGREP_BACKEND = pytest.StashKey[pytest.MonkeyPatch]()


def _run_semgrep_without_io_uring(config: pytest.Config) -> None:
    """Make every semgrep this run starts use Eio's posix backend, so a spent io_uring budget cannot fail it."""
    patch = pytest.MonkeyPatch()
    patch.setenv("EIO_BACKEND", "posix")
    config.stash[_SEMGREP_BACKEND] = patch


def pytest_unconfigure(config: pytest.Config) -> None:
    """Give back the environment pytest_configure set for semgrep."""
    patch = config.stash.get(_SEMGREP_BACKEND, None)
    if patch is not None:
        patch.undo()


def pytest_configure(config):
    """Scale both wall-clock deadlines by the machine's oversubscription.

    The per-test deadline has exactly one home — ``[tool.pytest.ini_options]
    timeout`` — so it is read from there rather than mirrored here, and the
    watchdog is derived from the scaled value so the two can never cross.
    """
    global _WATCHDOG_TIMEOUT, _WATCHDOG_BASE, _DEADLINE_SCALE  # noqa: PLW0603

    _run_semgrep_without_io_uring(config)
    # 期限の倍率は機体の混みから読むので、枠を取った後(走り始める拍の混み)で読む。
    _hold_check_slot(config)
    _turn_vm_oracle_on(bool(config.getini(VM_INVARIANT_CHECKS_INI)))

    _WATCHDOG_BASE = float(config.getini("watchdog_timeout"))
    _DEADLINE_SCALE = deadline_scale(config.getini("deadline_scale"), float(config.getini("deadline_scale_cap")))
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
            f"(load per usable CPU) — per test {scaled_per_test:.0f}s, "
            f"watchdog {_WATCHDOG_TIMEOUT}s\n",
            file=sys.stderr,
            flush=True,
        )


def _per_test_base_seconds(config) -> float:
    """The unscaled per-test deadline, in pytest-timeout's own precedence.

    Explicit beats declared: an env var or ``--timeout`` from the caller is
    the number they meant, and scaling theirs is right — silently replacing
    it with the ini default would make the escape hatch a lie.  0 = not set.

    The precedence (``--timeout``, then ``PYTEST_TIMEOUT``, then the ini) is
    pytest-timeout's own, so it is read from pytest-timeout rather than
    mirrored here — this file does not read the environment (#2901).
    """
    from pytest_timeout import get_env_settings

    return float(get_env_settings(config).timeout or 0.0)


def pytest_collection_modifyitems(config, items):
    """Scale per-test ``@pytest.mark.timeout(...)`` deadlines too.

    pytest-timeout gives a marker precedence over the ini, so scaling the ini
    alone would leave exactly the tests that declared themselves slow — the
    ones most likely to spawn a subprocess — on an unscaled deadline.

    pytest-timeout reads only the closest marker — the first one found — so the
    scaled marker goes in front of the declared one.  Appended behind it (as
    from 2026-08-17 to 2026-09-26) it was never read: a test's declared
    deadline stayed unscaled at every load (agora-redesign#639).
    """
    if _DEADLINE_SCALE <= 1.0:
        return
    for item in items:
        marker = item.get_closest_marker("timeout")
        declared = None if marker is None else _declared_seconds(marker)
        if declared is None:
            continue
        item.add_marker(_scaled_marker(marker, declared), append=False)


def _declared_seconds(marker: pytest.Mark) -> float | None:
    """The deadline a ``timeout`` marker declares, read the way pytest-timeout reads it.

    None when the marker leaves the deadline to the run (``timeout=None``).
    """
    declared = marker.args[0] if marker.args else marker.kwargs.get("timeout")
    if declared is None:
        return None
    try:
        return float(declared)
    except (TypeError, ValueError):
        # Not a number: pytest-timeout fails that test with its own message, so
        # there is nothing to scale.
        return None


def _scaled_marker(marker: pytest.Mark, declared: float) -> pytest.MarkDecorator:
    """The declared marker with its deadline scaled and its other settings (method) kept."""
    others = {name: value for name, value in marker.kwargs.items() if name != "timeout"}
    return pytest.mark.timeout(declared * _DEADLINE_SCALE, *marker.args[1:], **others)


def enforced_deadline(item: pytest.Item) -> float:
    """The per-test deadline pytest-timeout enforces on ``item``, in seconds (0 = none).

    The one home for pytest-timeout's precedence — the closest marker (already
    scaled at collection), else the run's deadline (already scaled in
    pytest_configure) — so the watchdog and a test that budgets its own
    subprocess read the number that is actually enforced.
    """
    marker = item.get_closest_marker("timeout")
    declared = None if marker is None else _declared_seconds(marker)
    if declared is not None:
        return declared
    return float(item.config.getoption("timeout", None) or 0)


@pytest.fixture
def per_test_deadline(request: pytest.FixtureRequest) -> float | None:
    """The deadline pytest-timeout enforces on the requesting test (None = no deadline)."""
    deadline = enforced_deadline(request.node)
    return deadline if deadline > 0 else None


@pytest.fixture
def deadline_scale_factor() -> float:
    """This run's load factor, for a deadline a test sets itself — a child process's ``timeout=``.

    The deadlines pytest-timeout enforces are scaled above; a number a test hands a
    subprocess is not, and stays fixed while the machine is oversubscribed (2026-10-11
    20:00: a 30 s child of packages/doeff-adr/tests/test_indexed_collection.py, 1.5 s
    alone, was killed — card ki-bb91f171d222).
    """
    return _DEADLINE_SCALE


def pytest_runtest_setup(item):
    """Reset watchdog at the start of each test."""
    _reset_watchdog(item.config, item.nodeid, _watchdog_timeout_for_item(item))


def pytest_runtest_teardown(item, nextitem):
    """Reset watchdog after each test (covers slow teardown)."""
    _reset_watchdog(item.config, item.nodeid, phase=" (teardown)")


def pytest_sessionfinish(session, exitstatus):
    """Stop watchdog when pytest finishes."""
    _stop_watchdog()


def _watchdog_timeout_for_item(item) -> int:
    """The watchdog for one test: clear of the deadline pytest-timeout enforces on it."""
    return int(max(_WATCHDOG_TIMEOUT, enforced_deadline(item) + 30.0))


# ---------------------------------------------------------------------------
# Machine premises: a test that needs something this machine may not have
# (agora-redesign #639, 2026-09-26).
#
# Two premises are asked through here, and each is named with its own word of
# the check layer:
#
#   machine_tool(name, *probe)      a tool that starts               tool-absent
#   machine_checkout(path, commit)  a git checkout holding a commit  premise-unmet
#
# A tool on PATH is not a working tool.  The daily run's pod has the router's
# entry point for `codex` on PATH but no binary behind it, so `shutil.which`
# said "present" and the real-codex test went red (exit 127) for a fact about
# the machine, not about the code.  Likewise the daily run's machine (zeus)
# has no custody checkout, and the test that compares the custody contract
# copy with its source at the pinned commit went red for the same kind of
# fact.  Each premise is observed once per session, and the test is skipped
# when it does not hold.
#
# A skip alone is silent: the run would read "measured, nothing red" for a
# test that never ran.  So at the end of the session the unmet premises are
# named as "not executed" in the check layer's own format — one line per skipped test
# (dotfiles agentcli remote_check — the file the land tool reads; it records
# the line as missing coverage, not as a red).  The format is read from the
# check layer on every run and never copied here: a copy keeps agreeing with
# itself on the day the layer changes its spelling.  Only the word is chosen
# here, by the side that observed the premise, and the layer is asked whether
# it knows the word.  On a machine with only a clone of this repository there
# is no check layer, and the skip reasons are all that is printed.
#
# Only the premise itself is asked here.  Once it holds, whatever the test
# finds is the test's own red: a tool that starts and then answers wrong (or
# lacks a login), a checkout whose content differs from what the test expects.
# ---------------------------------------------------------------------------
_CHECK_LAYER = Path.home() / "dotfiles" / "agentcli" / "src" / "agentcli" / "ai" / "entry" / "remote_check.py"
_CHECK_LAYER_MODULE = "doeff_check_layer_remote_check"
_PREMISE_UNMET = "machine premise unmet:"
#: The check layer's word for "the command's executable cannot be started here".
_TOOL_ABSENT = "tool-absent"
#: The check layer's word for "a premise of the check does not hold on this machine".
_CHECKOUT_ABSENT = "premise-unmet"
#: Which check-layer word names each unmet premise, by its skip detail.
_UNMET_WORDS = pytest.StashKey[dict[str, str]]()
_PROBE_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class _PremiseHolds:
    """The premise holds: the test may use the tool or checkout at ``path``."""

    path: str


@dataclass(frozen=True)
class _PremiseUnmet:
    """The premise does not hold here: ``detail`` becomes the skip reason."""

    detail: str


def _premise_unmet(config: pytest.Config, word: str, detail: str) -> NoReturn:
    """Skip the test for an unmet machine premise, remembering the check-layer word for it."""
    config.stash.setdefault(_UNMET_WORDS, {})[detail] = word
    pytest.skip(f"{_PREMISE_UNMET} {detail}")


def _probe_tool(name: str, probe: Sequence[str]) -> _PremiseHolds | _PremiseUnmet:
    """Start ``name`` once with ``probe`` and read whether it answered with exit 0."""
    path = shutil.which(name)
    if path is None:
        # The searched PATH tells which machine's environment lacked the tool (agora-redesign #3870: the daily run
        # went to zeus, not to the pod whose image held the tool).
        searched = os.pathsep.join(os.get_exec_path())
        return _PremiseUnmet(f"{name} is not on PATH (searched: {searched})")
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
        return _PremiseUnmet(
            f"{name} ({path}) did not answer `{asked}` within {_PROBE_TIMEOUT_S:.0f}s"
        )
    except OSError as error:
        return _PremiseUnmet(f"{name} ({path}) cannot be started: {error}")
    if answer.returncode != 0:
        # The last line is the tool's final word (a router's "no binary behind it").
        said = (answer.stderr.strip() or answer.stdout.strip()).splitlines()
        last_line = said[-1] if said else "no output"
        return _PremiseUnmet(f"{name} ({path}) exits {answer.returncode}: {last_line}")
    return _PremiseHolds(path)


def _probe_checkout(git: str, path: Path, commit: str) -> _PremiseHolds | _PremiseUnmet:
    """Read whether ``path`` is a git checkout that already holds ``commit`` (fetches nothing)."""
    if not path.is_dir():
        return _PremiseUnmet(f"checkout {path} is missing")
    try:
        answer = subprocess.run(
            [git, "-C", str(path), "cat-file", "-e", f"{commit}^{{commit}}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _PremiseUnmet(f"checkout {path} did not answer within {_PROBE_TIMEOUT_S:.0f}s")
    except OSError as error:
        return _PremiseUnmet(f"checkout {path} cannot be read: {error}")
    if answer.returncode != 0:
        said = answer.stderr.strip().splitlines()
        return _PremiseUnmet(
            f"checkout {path} does not hold commit {commit}" + (f": {said[-1]}" if said else "")
        )
    return _PremiseHolds(str(path))


@pytest.fixture(scope="session")
def machine_tool(request: pytest.FixtureRequest) -> Callable[..., str]:
    """``machine_tool("codex")`` → the path of a tool that starts on this machine, or skip.

    Extra arguments replace the probe (default ``--version``).  Each tool is
    probed once per session.  An unmet one is named tool-absent.
    """
    answers: dict[tuple[str, ...], _PremiseHolds | _PremiseUnmet] = {}

    def require(name: str, *probe: str) -> str:
        """Give the test a tool that starts, or skip it as an unmet machine premise."""
        asked = probe or ("--version",)
        key = (name, *asked)
        if key not in answers:
            answers[key] = _probe_tool(name, asked)
        match answers[key]:
            case _PremiseHolds(path=path):
                return path
            case _PremiseUnmet(detail=detail):
                _premise_unmet(request.config, _TOOL_ABSENT, detail)

    return require


@pytest.fixture(scope="session")
def machine_checkout(
    request: pytest.FixtureRequest, machine_tool: Callable[..., str]
) -> Callable[[Path, str], Path]:
    """``machine_checkout(path, commit)`` → a git checkout at ``path`` holding ``commit``, or skip.

    git itself is a tool premise (asked through ``machine_tool``).  A missing
    checkout, or one that has not fetched ``commit``, is named premise-unmet.
    Each (path, commit) is probed once per session.
    """
    answers: dict[tuple[Path, str], _PremiseHolds | _PremiseUnmet] = {}

    def require(path: Path, commit: str) -> Path:
        """Give the test a checkout that holds ``commit``, or skip it as an unmet premise."""
        git = machine_tool("git")
        key = (path, commit)
        if key not in answers:
            answers[key] = _probe_checkout(git, path, commit)
        match answers[key]:
            case _PremiseHolds(path=held):
                return Path(held)
            case _PremiseUnmet(detail=detail):
                _premise_unmet(request.config, _CHECKOUT_ABSENT, detail)

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


def _unmet_premises(
    skipped: Sequence[pytest.TestReport], words: dict[str, str]
) -> dict[str, list[str]]:
    """``"<nodeid>: <detail>"`` of every test skipped for an unmet premise, by check-layer word."""
    unmet: dict[str, list[str]] = {}
    for report in skipped:
        longrepr = report.longrepr
        if not (isinstance(longrepr, tuple) and len(longrepr) == 3):
            continue
        reason = str(longrepr[2]).removeprefix("Skipped: ")
        detail = reason.removeprefix(_PREMISE_UNMET).strip()
        if reason.startswith(_PREMISE_UNMET) and detail in words:
            unmet.setdefault(words[detail], []).append(f"{report.nodeid}: {detail}")
    return unmet


def pytest_terminal_summary(terminalreporter):
    """Name the unmet machine premises as not executed: one line per skipped test.

    One line per test, not one per check-layer word: the check layer folds a
    reason to 300 characters, so a line that joined several tests lost the
    node ids at its end (agora-redesign#645 item 5).
    """
    unmet = _unmet_premises(
        terminalreporter.stats.get("skipped", []),
        terminalreporter.config.stash.get(_UNMET_WORDS, {}),
    )
    if not unmet:
        return
    terminalreporter.ensure_newline()
    layer = _check_layer()
    unknown: dict[str, list[str]] = {}
    for word, named in unmet.items():
        if layer is not None and word in layer.UNEXECUTED_KINDS:
            for entry in named:
                terminalreporter.write(layer.unexecuted_line([word], entry))
        else:
            unknown[word] = named
    if not unknown:
        return
    if layer is None:
        where = "no check layer here"
    else:
        where = f"the check layer at {_CHECK_LAYER} does not know {', '.join(unknown)}"
    named = [entry for entries in unknown.values() for entry in entries]
    terminalreporter.write_line(
        f"{len(named)} test(s) skipped for a machine premise ({where}): {'; '.join(named)}"
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
# brain to ask, so the hook lets the run through without a word: gating it
# there would mean no test could ever run, and a line about a toolchain the
# machine does not have would open every run of an outside contributor (the
# machine check slot of doeff_hy.check_slot is silent there too).  Where the
# toolchain *is* present the hook is fail-closed (an unreadable policy or an
# unobtainable grant stops the session before a single test body runs).
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


def _hold_check_slot(config):
    """収集の前に、機体の検の枠を 1 つ取る(埋まっていれば待つ)— 受付の正本の hold_check_slot へ委譲する(card ki-aabe38bf85ef)。

    枠の値(本数・置き場・待ちの上限)と取り方は正本 1 点(~/dotfiles/agent/tests/machine_check_slots.py と隣の .json)が持ち、
    ここは呼ぶだけ。受付は本体の前(pytest_collection_finish)にも同じ枠を取るが、Hy の検の収集は CPU を数分使うので収集の前に取る
    (同じ config の 2 度目は何もしない・xdist の親は取らず子が取る)。正本が無い宿・口の無い古い正本の宿では取らない。
    """
    admission = _broad_run_admission()
    hold = getattr(admission, "hold_check_slot", None) if admission is not None else None
    if hold is not None:
        hold(config)


def pytest_collection_finish(session):
    """収集の後・本体の前の受付(広範囲なら許可を 1 回消費できた時だけ走らせる)。正本の無い宿は黙って素通し。"""
    admission = _broad_run_admission()
    if admission is not None:
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
#
# The settings reach only this process and the subprocesses that inherit its
# environment; a test can still hand a subprocess a bytecode directory of its
# own inside the checkout, and every test above it stays green (agora-redesign
# #639, blind review B: 0 -> 131 files left in the tree).  The land tool keeps
# the ignored files of its checkout from one candidate to the next, so bytecode
# left there is read by later runs of other commits.  The run therefore also
# fails when a bytecode file under the checkout was written during the run —
# judged by the files themselves, not by how a subprocess was configured.  A
# subprocess that needs bytecode gets a directory under pytest's temporary
# directory (tmp_path_factory) instead.
#
# "Under the checkout" leaves out the one directory that is not the tree under
# test: the environment of the interpreter running the tests (the .venv).  The
# walk of its 13,000 files in 2,000 directories took 9-34 s at the end of a run on a
# loaded machine when the directories were not in the operating system's cache
# (card ki-16954373dfe0, 2026-10-12: the last test's teardown of a whole test file,
# load 65, out of the 60 s of `make test-changed`; measured again at load 66-75: 12.7 s
# for one test, 34 s for the function alone).
# Inside it only the bytecode of the modules a start of the interpreter imports
# is looked at (the list of the section below, one stat each): those are what a
# subprocess without the pin writes first, and six of them were found by the
# daily run.  The bytecode of the other installed (third-party) packages is not
# looked at: the workspace's own packages are editable installs, whose sources
# are in the tree and are walked.
# ---------------------------------------------------------------------------
_CHECKOUT = Path(__file__).resolve().parent
#: Directories listed at once when the run's end looks for bytecode (threads waiting for the disk, not CPU).
_WALK_THREADS = 32


def _written_since(path: Path, since: float) -> bool:
    """Whether ``path`` was modified at ``since`` or later (a file that is gone was not left behind)."""
    # A file another process removes between the listing and the stat was not left behind.
    with suppress(FileNotFoundError):
        return path.stat().st_mtime >= since
    return False


@dataclass(frozen=True)
class _Listing:
    """What one directory holds for the walk: its subdirectories and its bytecode files written since."""

    directories: list[Path]
    files: list[Path]


def _list_directory(directory: Path, since: float) -> _Listing:
    """The subdirectories of ``directory`` (``.git`` left out) and its bytecode files written at ``since`` or later.

    A directory that cannot be listed (removed meanwhile, not readable) has none, as in ``os.walk``.
    A symbolic link is not followed.
    """
    try:
        with os.scandir(directory) as scanned:
            entries = list(scanned)
    except OSError:
        return _Listing(directories=[], files=[])
    return _Listing(
        directories=[
            Path(entry.path)
            for entry in entries
            if entry.is_dir(follow_symlinks=False) and entry.name != ".git"
        ],
        files=[
            Path(entry.path)
            for entry in entries
            if entry.name.endswith(".pyc")
            and not entry.is_dir(follow_symlinks=False)
            and _written_since(Path(entry.path), since)
        ],
    )


def bytecode_written_since(root: Path, since: float, skip: Collection[Path] = ()) -> list[str]:
    """Find the bytecode files a run left under ``root`` (paths relative to it, sorted).

    A file counts when its modification time is ``since`` or later; ``.git`` and the
    directories in ``skip`` (paths as this walk builds them from ``root``) are not searched.

    Many directories are listed at once.  One that is not in the operating system's cache is read
    from the disk, and listing the 900 directories of a tree one at a time took 2.7-12.8 s on a
    loaded machine (load 65, 2026-10-12, card ki-16954373dfe0), against 0.6-1.3 s by 32 threads.
    """
    with ThreadPoolExecutor(_WALK_THREADS) as pool:
        return sorted(
            str(path.relative_to(root)) for path in _walk_for_bytecode(pool, root, since, skip)
        )


def _walk_for_bytecode(
    pool: ThreadPoolExecutor, root: Path, since: float, skip: Collection[Path]
) -> Iterator[Path]:
    """The bytecode files under ``root`` written at ``since`` or later, as the listings of its directories finish.

    A directory is queued as soon as the listing of its parent has finished (not when its whole
    level has), so the threads stay busy while a slow directory is still being read.
    """
    queued = {pool.submit(_list_directory, root, since)}
    while queued:
        finished, queued = wait(queued, return_when=FIRST_COMPLETED)
        for listing in finished:
            found = listing.result()
            yield from found.files
            queued |= {
                pool.submit(_list_directory, directory, since)
                for directory in found.directories
                if directory not in skip
            }


def bytecode_left_by_the_run(root: Path, since: float, startup: Sequence[Path]) -> list[str]:
    """Find the bytecode files a run left under ``root``, without walking the interpreter's environment.

    The environment of the interpreter running the tests (its ``sys.prefix``, the .venv) is the
    one directory of the checkout this does not search: the installed packages are 13,000 files in
    2,000 directories, and listing them took 9-34 s at the end of a run on a loaded machine
    (card ki-16954373dfe0).  What a run can write there that matters is the bytecode of the modules
    a start of the interpreter imports (the .pth files' ``import`` lines), which ``startup`` lists
    (see ``startup_bytecode``) and which is looked at file by file instead.
    """
    root = root.resolve()
    environments = frozenset(
        environment
        for environment in (Path(prefix).resolve() for prefix in (sys.prefix, sys.base_prefix))
        if environment != root and environment.is_relative_to(root)
    )
    written_at_startup = (
        str(path.relative_to(root)) for path in startup if _written_since(path, since)
    )
    return sorted({*bytecode_written_since(root, since, skip=environments), *written_at_startup})


@pytest.fixture(scope="session", autouse=True)
def _bytecode_settings_pinned(request: pytest.FixtureRequest):
    started = time.time()
    # Only a run that is not split looks per test (below); a pytest-xdist worker finds the list itself.
    startup = request.config.stash.get(_STARTUP_BYTECODE, None)
    if startup is None:
        timeout = _STARTUP_PROBE_TIMEOUT_S * _DEADLINE_SCALE
        startup = startup_bytecode(_CHECKOUT, sys.executable, timeout)
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
    written = bytecode_left_by_the_run(_CHECKOUT, started, startup)
    assert written == [], (
        f"{len(written)} bytecode file(s) were written under the checkout during this run "
        f"(first: {written[:5]}) — a subprocess that needs bytecode gets a directory under "
        f"pytest's temporary directory (tmp_path_factory), never one inside the checkout"
    )


# ---------------------------------------------------------------------------
# The test that wrote the startup bytecode is the one named.
#
# A subprocess started with an environment of its own (EnvMode.REPLACE with its allow-list, a
# test's own ``env=``) does not get the pin above, and the first thing a Python started without it
# does is write the bytecode of the modules its startup imports — the ``import`` lines of the .pth
# files in site-packages: the .venv's _virtualenv, an editable install's finder,
# doeff_hy_bytecode_guard — beside their sources in the checkout.  The check at the end of the
# session then failed under the name of the session's last test, and only in the daily run (a new
# checkout and .venv under the pin): a developer's tree already held that bytecode, written by an
# earlier run without the pin, so the subprocess rewrote nothing there.  Six writers were found one
# at a time this way, each a day later and under an unrelated test's name (2026-10-10 to 10-12,
# cf46518d5 to 48d59512d — card ki-23c3280da1a5).
#
# So the run removes the startup bytecode under the checkout before its tests start, and looks for
# it again after each phase (setup, call, teardown) of each test.  A file that is back was written
# by that test — its body, or a fixture of any scope set up for it or torn down after it — and the
# test fails at its teardown under its own name, on any tree.  The file is removed again, so the
# next writer is named too.  The startup modules are read from a start of this interpreter (with
# -B, so the reading writes nothing) rather than listed here.  Hooks rather than an autouse
# fixture, so a Hy script item (a plain pytest.Item, without fixtures) is watched as well.
#
# Not under pytest-xdist: the tests of the other workers run at the same time, so a file that
# appears during a test cannot be laid to it (the check at the end of the session still holds).
# A process outside this run that starts this checkout's Python without the pin while the run
# goes on is named as a test too.
# ---------------------------------------------------------------------------
_LIST_STARTUP_SOURCES = (
    "import sys\n"
    "for module in list(sys.modules.values()):\n"
    "    print(getattr(module, '__file__', None) or '')\n"
)
_STARTUP_PROBE_TIMEOUT_S = 30.0
_STARTUP_BYTECODE = pytest.StashKey[tuple[Path, ...]]()
_STARTUP_BYTECODE_WRITTEN = pytest.StashKey[tuple[tuple[str, tuple[Path, ...]], ...]]()


def startup_bytecode(root: Path, python: str, timeout: float) -> tuple[Path, ...]:
    """The bytecode files a start of ``python`` that may write bytecode leaves under ``root``.

    One file per module the start imports from under ``root``, at the standard location beside
    its source (a subprocess with an environment of its own has no PYTHONPYCACHEPREFIX).
    """
    tag = sys.implementation.cache_tag
    if tag is None:  # this Python writes no bytecode at all
        return ()
    listed = subprocess.run(
        [python, "-B", "-c", _LIST_STARTUP_SOURCES],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    sources = {Path(line).resolve() for line in listed.stdout.splitlines() if line.endswith(".py")}
    return tuple(
        sorted(
            source.parent / "__pycache__" / f"{source.stem}.{tag}.pyc"
            for source in sources
            if source.is_relative_to(root)
        )
    )


def _remove_present(files: Sequence[Path]) -> tuple[Path, ...]:
    """Remove those of ``files`` that exist, and return them — the ones a test wrote."""
    return tuple(path for path in files if _removed(path))


def _removed(path: Path) -> bool:
    """Remove ``path`` if it exists, saying whether it did (another process may remove it first)."""
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


@pytest.hookimpl(wrapper=True)
def pytest_runtestloop(session: pytest.Session) -> Generator[None, object, object]:
    """Remove the startup bytecode under the checkout before the tests start, and watch for it."""
    config = session.config
    if not (hasattr(config, "workerinput") or getattr(config.option, "numprocesses", None)):
        timeout = _STARTUP_PROBE_TIMEOUT_S * _DEADLINE_SCALE
        files = startup_bytecode(_CHECKOUT, sys.executable, timeout)
        _remove_present(files)
        config.stash[_STARTUP_BYTECODE] = files
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Fail a test at its teardown when the startup bytecode came back during one of its phases."""
    report = yield
    written = _remove_present(item.config.stash.get(_STARTUP_BYTECODE, ()))
    if written:
        noted = item.stash.get(_STARTUP_BYTECODE_WRITTEN, ())
        item.stash[_STARTUP_BYTECODE_WRITTEN] = (*noted, (call.when, written))
    if call.when == "teardown" and _STARTUP_BYTECODE_WRITTEN in item.stash:
        message = _startup_bytecode_message(item.stash[_STARTUP_BYTECODE_WRITTEN])
        if report.failed:  # the teardown failed on its own: keep its error and add this one
            report.sections = [*report.sections, ("startup bytecode written", message)]
        else:
            report.outcome = "failed"
            report.longrepr = message
    return report


def _startup_bytecode_message(written: Sequence[tuple[str, tuple[Path, ...]]]) -> str:
    """Tell the test's author which files the test wrote in which phase, and how to stop it."""
    phases = "\n".join(
        f"  {when}: {', '.join(str(path.relative_to(_CHECKOUT)) for path in paths)}"
        for when, paths in written
    )
    return (
        "this test wrote the bytecode of the interpreter's startup under the checkout:\n"
        f"{phases}\n"
        "A subprocess started Python without -B and without PYTHONDONTWRITEBYTECODE=1 (an "
        "environment of its own leaves out the root conftest's pin): start it with -B, or put "
        "PYTHONDONTWRITEBYTECODE=1 in its environment.  Setup and teardown include the fixtures of "
        "any scope set up for this test or torn down after it."
    )


# ---------------------------------------------------------------------------
# A bytecode directory for the subprocesses of one test (opt-in).
#
# Under the pin above a subprocess writes no bytecode and looks for it only next
# to the source, where nothing compiled the modules that only subprocesses import
# (an entry point such as doeff_cluster.worker.entry.env_tool), and where nothing is found
# at all when the developer's PYTHONPYCACHEPREFIX took the collection's bytecode
# elsewhere.  Each such subprocess compiled its Hy modules from source again:
# 4-9 s per process on zeus, most of test_env_careful.hy's time (agora-redesign
# #2818, measured 2026-10-02).  On a deployed worker those subprocesses find
# bytecode (the runtime-env preparation compiles the code inside an env root, and
# a job's process may write the rest), so the time existed only under test
# (agora-redesign #2833).
#
# A test whose subprocesses need bytecode asks for ``subprocess_bytecode``: its
# subprocesses then write and read bytecode in one directory of this run under
# pytest's temporary directory (never the checkout, which the pin still checks).
# Hy's compiled-code store is left as the environment sets it.  Opt-in rather
# than the default, so a test that measures compiling (doeff-hy-pytest's budget
# tests) still runs every subprocess cold and gives the same answer in every run.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def _subprocess_bytecode_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("subprocess-bytecode")


@pytest.fixture
def subprocess_bytecode(_subprocess_bytecode_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Let the subprocesses this test starts write and reuse bytecode in this run's directory.

    Sets ``PYTHONPYCACHEPREFIX`` to the directory and removes ``PYTHONDONTWRITEBYTECODE``
    from the environment the subprocesses inherit, for this test only.  This process keeps
    the pinned settings.  A subprocess started with an environment of its own (not
    inherited) gets neither.
    """
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(_subprocess_bytecode_dir))
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    return _subprocess_bytecode_dir
