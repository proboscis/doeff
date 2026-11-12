"""Test size classes (tiers): small, medium, large -- choosing tests by size and reporting small tests that act like bigger ones.

Each collected test gets one tier, decided in this order:

1. A tier mark on the test (`tier_small`, `tier_medium`, `tier_large` -- deftest's `:tier` writes it; a Python test writes
   `@pytest.mark.tier_medium`). With more than one, the largest wins.
2. The ini setting `doeff_tier_by_marker` (1 line = marker=tier, e.g. `real_world=medium`): the largest tier among the test's marks.
3. The ini setting `doeff_tier_default` (default `small`).

So a repo whose existing tests already carry marks for their kind gets every test's tier from the settings, without rewriting a test.

The tier is a mark name rather than a mark argument because doeff-hy's item record (collection without importing the test module)
keeps only mark names.

`--doeff-tier small` (or `small,medium`) keeps only the tests of the named tiers; the rest are deselected. Without it nothing is deselected.

A small test is meant to run in one process without I/O beyond its own memory and files, in under a minute. While a small test's
call phase runs, an audit hook (`sys.addaudithook`) watches for child processes and network sockets; when the call phase is over
`doeff_tier_small_seconds` (default 60) wall-clock seconds, or used one of them, the test is listed at the end of the run under
"doeff test tiers". The list is report only: the test's outcome does not change. The finding rides on the test report
(`user_properties`), so a run split over processes (pytest-xdist) lists it in the controlling process too.
"""

import functools
import sys
import threading
from collections.abc import Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

import pytest

Tier = Literal["small", "medium", "large"]
TIERS: tuple[Tier, ...] = ("small", "medium", "large")
TIER_MARK_PREFIX = "tier_"

TIER_OPTION = "--doeff-tier"
TIER_DEST = "doeff_tier"
TIER_BY_MARKER_INI = "doeff_tier_by_marker"
TIER_DEFAULT_INI = "doeff_tier_default"
SMALL_SECONDS_INI = "doeff_tier_small_seconds"
DEFAULT_SMALL_SECONDS = 60.0
FINDING_PROPERTY = "doeff_tier_finding"

# Audit events that mean a test leaves its own process: starting another program, and opening a network conversation.
# Creating a socket alone (`socket.__new__`) is not one -- asyncio's event loop makes a socket pair for its own wake-ups.
CHILD_PROCESS_EVENTS = frozenset(
    {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.exec", "pty.spawn"}
)
SOCKET_EVENTS = frozenset({"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname"})


@dataclass(frozen=True)
class SettingError:
    """A setting value that cannot be read (the message says what was expected)."""

    message: str


def parse_tier(raw: str) -> Tier | SettingError:
    """Read one tier name."""
    for tier in TIERS:
        if raw == tier:
            return tier
    return SettingError(f"one of {', '.join(TIERS)}: {raw!r}")


def parse_tier_by_marker(lines: Sequence[str]) -> Mapping[str, Tier] | SettingError:
    """Read the marker=tier lines (blank lines are skipped). A marker named twice or an unreadable line is an error."""
    table: dict[str, Tier] = {}
    for line in lines:
        text = line.strip()
        if not text:
            continue
        marker, sep, raw = text.partition("=")
        marker = marker.strip()
        if not sep or not marker:
            return SettingError(f"{TIER_BY_MARKER_INI}: a line is marker=tier: {line!r}")
        match parse_tier(raw.strip()):
            case SettingError(message=message):
                return SettingError(f"{TIER_BY_MARKER_INI}: the tier of {marker} must be {message}")
            case tier:
                if marker in table:
                    return SettingError(f"{TIER_BY_MARKER_INI}: {marker} is named twice")
                table[marker] = tier
    return table


def parse_tier_selection(raw: str) -> frozenset[Tier] | SettingError:
    """Read the value of --doeff-tier: tier names separated by commas."""
    chosen: set[Tier] = set()
    for part in raw.split(","):
        match parse_tier(part.strip()):
            case SettingError(message=message):
                return SettingError(f"{TIER_OPTION} takes tier names separated by commas, each {message}")
            case tier:
                chosen.add(tier)
    return frozenset(chosen)


def _largest(tiers: Iterable[Tier]) -> Tier | None:
    found = [TIERS.index(t) for t in tiers]
    return TIERS[max(found)] if found else None


def tier_of(markers: Iterable[str], by_marker: Mapping[str, Tier], default: Tier) -> Tier:
    """The tier of a test with these mark names: its own tier mark, else the setting for its marks, else the default."""
    names = list(markers)
    declared = _largest(
        tier for name in names if name.startswith(TIER_MARK_PREFIX) for tier in TIERS if name == TIER_MARK_PREFIX + tier
    )
    if declared is not None:
        return declared
    mapped = _largest(by_marker[name] for name in names if name in by_marker)
    return mapped if mapped is not None else default


@dataclass(frozen=True)
class TierSettings:
    """The settings read once at configure."""

    by_marker: Mapping[str, Tier]
    default: Tier
    selected: frozenset[Tier] | None
    small_seconds: float


@dataclass(frozen=True)
class SmallFinding:
    """A small test that acted like a bigger one, and what it did (the reasons joined by "; ")."""

    nodeid: str
    reasons: str


@dataclass
class _Watch:
    """The events seen while one small test's call phase runs (the first argument of each event, shortened)."""

    seen: dict[str, str] = field(default_factory=dict)


class _AuditWatcher:
    """One audit hook for the process (an audit hook cannot be removed), active only while a small test's call phase runs.

    Events from other threads count too -- a thread the test started does its I/O there -- but only while a watch is open.
    Watches nest (a test that runs pytest in its own process opens one inside its own); an event goes to the innermost."""

    def __init__(self) -> None:
        self._watches: list[_Watch] = []
        self._lock = threading.Lock()
        sys.addaudithook(self._hook)

    def _hook(self, event: str, args: tuple[object, ...]) -> None:
        if not self._watches or (event not in CHILD_PROCESS_EVENTS and event not in SOCKET_EVENTS):
            return
        with self._lock:
            if not self._watches:
                return
            watch = self._watches[-1]
            if event not in watch.seen:
                first = repr(args[0]) if args else ""
                watch.seen[event] = first if len(first) <= 80 else first[:77] + "..."

    def open(self) -> _Watch:
        watch = _Watch()
        with self._lock:
            self._watches.append(watch)
        return watch

    def close(self, watch: _Watch) -> None:
        with self._lock:
            self._watches.remove(watch)


@functools.cache
def _watcher() -> _AuditWatcher:
    """The process's one watcher, made (and its audit hook added) at the first small test."""
    return _AuditWatcher()


def finding_reasons(seen: Mapping[str, str], call_seconds: float, small_seconds: float) -> tuple[str, ...]:
    """The lines that say why a small test is not small (empty = it acted small)."""
    reasons: list[str] = []
    if call_seconds > small_seconds:
        reasons.append(f"ran {call_seconds:.1f} s (small is up to {small_seconds:g} s)")
    children = sorted(e for e in seen if e in CHILD_PROCESS_EVENTS)
    sockets = sorted(e for e in seen if e in SOCKET_EVENTS)
    if children:
        reasons.append("started a child process (" + ", ".join(f"{e} {seen[e]}" for e in children) + ")")
    if sockets:
        reasons.append("used a network socket (" + ", ".join(f"{e} {seen[e]}" for e in sockets) + ")")
    return tuple(reasons)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the tier option and settings with pytest."""
    parser.addoption(
        TIER_OPTION,
        dest=TIER_DEST,
        default=None,
        help="Run only the tests of these tiers (small, medium, large; several separated by commas). Without it, every tier runs",
    )
    parser.addini(
        TIER_BY_MARKER_INI,
        "Tier of a test without a tier mark, by its marks (1 line = marker=tier; with several, the largest tier)",
        type="linelist",
        default=[],
    )
    parser.addini(TIER_DEFAULT_INI, "Tier of a test with neither a tier mark nor a mapped mark (default small)", default="small")
    parser.addini(
        SMALL_SECONDS_INI,
        "Wall-clock seconds of a small test's call phase above which it is listed as not small (default 60)",
        default="",
    )


def _settings(config: pytest.Config) -> TierSettings:
    """Read the settings (an unreadable setting stops the run instead of falling back to a default)."""
    match parse_tier_by_marker([str(line) for line in config.getini(TIER_BY_MARKER_INI)]):
        case SettingError(message=message):
            raise pytest.UsageError(message)
        case by_marker:
            pass
    match parse_tier(str(config.getini(TIER_DEFAULT_INI)).strip()):
        case SettingError(message=message):
            raise pytest.UsageError(f"{TIER_DEFAULT_INI} must be {message}")
        case default:
            pass
    raw_selection = config.getoption(TIER_DEST)
    selected: frozenset[Tier] | None = None
    if raw_selection is not None:
        match parse_tier_selection(str(raw_selection)):
            case SettingError(message=message):
                raise pytest.UsageError(message)
            case chosen:
                selected = chosen
    raw_seconds = str(config.getini(SMALL_SECONDS_INI)).strip()
    small_seconds = DEFAULT_SMALL_SECONDS
    if raw_seconds:
        try:
            small_seconds = float(raw_seconds)
        except ValueError:
            raise pytest.UsageError(f"{SMALL_SECONDS_INI} must be a positive number of seconds: {raw_seconds!r}") from None
        if small_seconds <= 0:
            raise pytest.UsageError(f"{SMALL_SECONDS_INI} must be a positive number of seconds: {raw_seconds!r}")
    return TierSettings(by_marker, default, selected, small_seconds)


_TIER_KEY = pytest.StashKey[Tier]()
_WATCH_KEY = pytest.StashKey[_Watch]()


class TierPlugin:
    """The per-run part: the tiers of the collected items, the selection and the findings (one per pytest run, so a pytest run inside a test keeps its own)."""

    def __init__(self, settings: TierSettings) -> None:
        self.settings = settings
        self.collected: dict[Tier, int] = dict.fromkeys(TIERS, 0)
        self.selected: dict[Tier, int] = dict.fromkeys(TIERS, 0)
        self.findings: list[SmallFinding] = []

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        """Give every item its tier; with --doeff-tier, deselect the items of other tiers. Runs after -m and -k have deselected theirs."""
        kept: list[pytest.Item] = []
        dropped: list[pytest.Item] = []
        for item in items:
            tier = tier_of([mark.name for mark in item.iter_markers()], self.settings.by_marker, self.settings.default)
            item.stash[_TIER_KEY] = tier
            self.collected[tier] += 1
            if self.settings.selected is None or tier in self.settings.selected:
                kept.append(item)
                self.selected[tier] += 1
            else:
                dropped.append(item)
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = kept

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, None, None]:
        """Watch a small test's call phase for child processes and network sockets."""
        if item.stash.get(_TIER_KEY, None) != "small":
            return (yield)
        watcher = _watcher()
        watch = watcher.open()
        item.stash[_WATCH_KEY] = watch
        try:
            return (yield)
        finally:
            watcher.close(watch)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(
        self, item: pytest.Item, call: pytest.CallInfo[None]
    ) -> Generator[None, pytest.TestReport, pytest.TestReport]:
        """Put the finding of a small test that acted bigger on its call report."""
        report = yield
        if call.when != "call" or item.stash.get(_TIER_KEY, None) != "small":
            return report
        watch = item.stash.get(_WATCH_KEY, None)
        reasons = finding_reasons(watch.seen if watch is not None else {}, call.duration, self.settings.small_seconds)
        if reasons:
            report.user_properties.append((FINDING_PROPERTY, "; ".join(reasons)))
        return report

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Gather the findings (in the controlling process too when the run is split over processes)."""
        if report.when != "call":
            return
        for name, value in report.user_properties:
            if name == FINDING_PROPERTY:
                self.findings.append(SmallFinding(report.nodeid, str(value)))

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        """Print the count of tests per tier when a tier was named, and the small tests that acted bigger."""
        if self.settings.selected is None and not self.findings:
            return
        terminalreporter.section("doeff test tiers")
        if self.settings.selected is not None:
            chosen = ",".join(t for t in TIERS if t in self.settings.selected)
            terminalreporter.line(
                f"selected tiers: {chosen}; tests per tier (selected / collected): "
                + ", ".join(f"{t} {self.selected[t]} / {self.collected[t]}" for t in TIERS)
            )
        if self.findings:
            terminalreporter.line(
                f"small tests that are not small: {len(self.findings)} (report only -- declare their tier or make them small)"
            )
            for finding in self.findings:
                terminalreporter.line(f"  {finding.nodeid}: {finding.reasons}")


def pytest_configure(config: pytest.Config) -> None:
    """Register the tier marks, read the settings and register this run's tier plugin."""
    for tier in TIERS:
        config.addinivalue_line("markers", f"{TIER_MARK_PREFIX}{tier}: the test's size class is {tier} (doeff-hy-pytest --doeff-tier)")
    config.pluginmanager.register(TierPlugin(_settings(config)), "doeff_hy_tier_run")
