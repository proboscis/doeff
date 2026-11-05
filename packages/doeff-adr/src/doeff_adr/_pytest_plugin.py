"""Pytest plugin for executable ADR Hy files."""

import fnmatch
import functools
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import re
import sys
import types
import warnings
from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from doeff_hy._pytest_items import FunctionItem
from hy.importer import HyLoader

from doeff_adr._indexed_pytest import IndexedFunctions, supports_indexed_collection
from doeff_adr._item_cache import (
    ExpansionUnknown,
    PendingEntry,
    expansion_of,
    forget_cached,
    items_cache_dir_for,
    pending_entry,
    write_cached,
)
from doeff_adr._lazy_collection import (
    Indexed,
    NeedsImport,
    RecordMismatch,
    check_no_unrecorded_items,
    import_module_for,
    plan_collection,
    stub_module,
    swap_in_real_function,
    swap_in_real_module_marks,
    verify_records,
)
from doeff_adr._source_dependencies import DependencyChecks

DEFAULT_FILE_PATTERNS = (
    "defadr_*.hy",
    "test_defadr_*.hy",
    "docs/adr/defadr_*.hy",
    "docs/adrs/defadr_*.hy",
)
IGNORED_DISCOVERY_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
WiringMode = Literal["off", "warn", "strict"]
WIRING_MODES = frozenset({"off", "warn", "strict"})
# The wiring walk covers the whole rootdir. A mis-anchored rootdir (observed
# 2026-09-02: a docs/adr suite without an ini file resolved rootdir to $HOME and
# every pytest run silently crawled the home directory for 60+ seconds, minutes
# under load) must abort loudly instead of hanging the run without output. A
# healthy project rootdir stays far below this bound.
DEFAULT_WIRING_MAX_DIRS = 50_000


class WiringWalkBudgetError(Exception):
    """Wiring discovery visited more directories than the configured budget."""

    def __init__(self, dirs_walked: int) -> None:
        super().__init__(str(dirs_walked))
        self.dirs_walked = dirs_walked


@dataclass(frozen=True)
class WiringVerified:
    """Every executable ADR under rootdir was reached by the session's collection."""

    executable_adrs: frozenset[Path]


@dataclass(frozen=True)
class WiringUncollected:
    """Executable ADRs exist that the session's collection did not reach."""

    uncollected: tuple[Path, ...]


@dataclass(frozen=True)
class WiringWalkAborted:
    """Discovery exceeded its directory budget, so nothing could be verified."""

    dirs_walked: int
    max_dirs: int


@dataclass(frozen=True)
class NotDefaultScope:
    """The session collected caller-chosen paths, not the configured default scope.

    Such a collection cannot speak for the default invocation in either
    direction: a narrower one misses ADRs the default scope reaches, and a wider
    one (``pytest tests docs/adr`` while testpaths lacks docs/adr) reaches ADRs
    the default scope would leave silent.
    """

    args: tuple[str, ...]


WiringVerdict = WiringVerified | WiringUncollected | WiringWalkAborted

# Wiring is a property of the collection *scope*, not of the selection: -k / -m /
# --deselect drop items after collection, and an ADR they deselect was still
# reached (the canonical doeff gate itself runs with ``-m 'not e2e'``). The files
# are therefore snapshotted before any deselection hook runs.
_COLLECTED_FILES_KEY = pytest.StashKey[frozenset[Path]]()
# One measurement per session: the collection-finish report and an in-session
# gate test read the same verdict instead of walking rootdir twice.
_WIRING_VERDICT_KEY = pytest.StashKey[WiringVerdict]()
# End-of-collection report: files collected from the record, and files collected by import with the reason.
_INDEXED_FILES_KEY = pytest.StashKey[list[Path]]()
_IMPORTED_FILES_KEY = pytest.StashKey[list[tuple[Path, str]]]()
_DEPENDENCY_CHECKS_KEY = pytest.StashKey[DependencyChecks]()
_PENDING_ENTRIES_KEY = pytest.StashKey[tuple[PendingEntry, ...]]()
_SESSION_PATHS_KEY = pytest.StashKey["SessionPaths"]()
# What was read from ini doeff_hy_test_skips at startup.
_HY_TEST_SKIPS_KEY = pytest.StashKey["HyTestSkips"]()


class DoeffAdrHookspecs:
    """Hooks doeff-adr exposes to other plugins."""

    @pytest.hookspec(firstresult=True)
    def pytest_doeff_import_hy_module(self, collector: pytest.Module) -> types.ModuleType | None:
        """Load the module of a Hy test file. Both loading during collection (files the record cannot explain) and loading at item setup
        (files collected from the record) go through here. A plugin that measures import time (the doeff-hy-pytest limit) wraps this hook
        with a wrapper."""
        raise NotImplementedError


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """Register the doeff-adr hooks with pytest."""
    pluginmanager.add_hookspecs(DoeffAdrHookspecs)


@pytest.hookimpl
def pytest_doeff_import_hy_module(collector: pytest.Module) -> types.ModuleType:
    """Default implementation that loads the module of a Hy test file. A file loaded during collection has its record checked against the real thing
    right after loading and saved to the cache (even if a time-limit wrapper fails later, the record of a module that did load is kept — judging and saving are separate matters)."""
    module = _import_hy_file(collector.path, session_paths(collector.config).root_resolved)
    if isinstance(collector, DoeffAdrHyFile):
        collector.after_import(module)
    return module


class HySourceFinder(importlib.abc.MetaPathFinder):
    """.hy import finder that keeps .hy modules away from pytest assert rewriting.

    When the pytest rewriter (``AssertionRewritingHook``) finds a module of a file named on the command line with a ``SourceFileLoader``
    spec, it re-reads it as Python with ``ast.parse``. The Hy loader is a subclass of ``SourceFileLoader``, so when another test file imports the named
    .hy by name it raises SyntaxError (easy to hit for files collected from the record, since they have not been read yet).
    Placed before the rewriter, it returns the .hy spec (the Hy loader) as is.
    It does not touch the loader itself (the ``SourceFileLoader`` hook wrapped by the bytecode watcher (#1292) keeps working).
    """

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.origin is not None and spec.origin.endswith(".hy"):
            return spec
        return None


_HY_SOURCE_FINDER = HySourceFinder()


def pytest_configure(config: pytest.Config) -> None:
    """Put the .hy finder at the front of the import finders — pytest places the rewriting finder before the plugin configure,
    so inserting at the front here lets it take .hy before the rewriter."""
    if _HY_SOURCE_FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _HY_SOURCE_FINDER)
    # Marker set by deftest :scans — registered by this plugin that collects deftests, so it is not a marker unknown to any repo.
    config.addinivalue_line("markers", "scans(*globs): dir or glob this test scans and reads (deftest :scans — used by selection tooling)")
    # Read the skip list once at startup — a line without a reason stops the run here (not re-read per collected file).
    config.stash[_HY_TEST_SKIPS_KEY] = parse_hy_test_skips(config.getini("doeff_hy_test_skips"))


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the .hy finder placed at configure (so it does not pile up when pytest runs many times in the same process)."""
    if _HY_SOURCE_FINDER in sys.meta_path:
        sys.meta_path.remove(_HY_SOURCE_FINDER)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "doeff_adr_hy_files",
        "Glob patterns for executable ADR Hy files collected by doeff-adr.",
        type="linelist",
        default=[],
    )
    parser.addini(
        "doeff_hy_test_files",
        "Glob patterns for Hy test files (deftest modules, or top-to-bottom scripts run as one test each) "
        "collected by doeff-adr's Hy file collector everywhere under rootdir (empty = none).",
        type="linelist",
        default=[],
    )
    parser.addini(
        "doeff_hy_test_skips",
        "Hy test files reported as skipped without importing them, one per line: "
        "'<rootdir-relative path> | <reason>'.",
        type="linelist",
        default=[],
    )
    parser.addini(
        "doeff_adr_items_cache",
        "Directory of the doeff-adr cache of Hy test item records "
        "(empty = $DOEFF_ADR_ITEMS_CACHE, else ~/.cache/doeff-adr/pytest-items).",
        default="",
    )
    parser.addini(
        "doeff_adr_wiring",
        "How to report executable ADR files that pytest did not collect: off, warn, or strict.",
        default="warn",
    )
    parser.addini(
        "doeff_adr_wiring_max_dirs",
        "Positive upper bound on directories the wiring verification walk may visit "
        "before aborting loudly (the walk covers the whole rootdir).",
        default=str(DEFAULT_WIRING_MAX_DIRS),
    )
    parser.addoption(
        "--doeff-adr-wiring",
        choices=sorted(WIRING_MODES),
        default=None,
        help="Override doeff-adr wiring verification mode (off, warn, or strict).",
    )


def _posix(text: str) -> str:
    """Convert an OS-separator path string to posix form (unchanged on posix)."""
    return text if os.sep == "/" else text.replace(os.sep, "/")


@dataclass(frozen=True)
class HyFileMatcher:
    """A matcher with the executable ADR patterns and rootdir prepared once.

    Candidates have the same meaning as fnmatch: the file name, the path from rootdir, and the absolute path (same as ``_matches_file_patterns``). Whether a path is under rootdir
    can be known from the string prefix, so it avoids repeated Path decomposition and reconstruction (``relative_to`` — 3 times per file in collection, once more in scanning).
    A path that is not textually under rootdir is resolved (realpath) and made relative, as before.
    """

    root: Path
    root_prefix: str
    regex: re.Pattern[str]

    @classmethod
    def from_root(cls, root: Path, patterns: Sequence[str]) -> "HyFileMatcher":
        """Build the public boundary for path handling against root and patterns."""
        return cls(root, str(root).rstrip(os.sep) + os.sep, _pattern_regex(tuple(patterns)))

    def relative_module_parts(self, path: Path) -> list[str] | None:
        return _relative_module_parts(path, self.root)

    def module_name(self, path: Path) -> str:
        return _module_name_for_path(path, self.root)

    def import_base(self, path: Path) -> Path:
        return _import_base_for_path(path, self.root)

    def discover(
        self, norecurse: Sequence[str] = (), max_dirs: int = DEFAULT_WIRING_MAX_DIRS
    ) -> set[Path]:
        """Scan executable ADRs with the same root and patterns."""
        return _discover_executable_adrs(self.root, (), norecurse, max_dirs, matcher=self)

    def relative_posix(self, text: str) -> str:
        """Path from rootdir (for matching and reporting). If textually under rootdir, slice it out; otherwise resolve it and make it relative."""
        if text.startswith(self.root_prefix):
            return _posix(text[len(self.root_prefix) :])
        return _relative_posix(Path(text), self.root)

    def matches_text(self, text: str, name: str) -> bool:
        """Whether the file at ``text`` (an absolute path string) matches the executable ADR patterns."""
        candidates = (name, self.relative_posix(text), _posix(text))
        return any(self.regex.match(os.path.normcase(candidate)) for candidate in candidates)

    def matches(self, path: Path) -> bool:
        return self.matches_text(str(path), path.name)


@functools.cache
def _matcher(root_text: str, patterns: tuple[str, ...]) -> HyFileMatcher:
    """Prepare the matcher once per rootdir and pattern set."""
    root = Path(root_text)
    return HyFileMatcher.from_root(root, patterns)


@dataclass(frozen=True)
class SessionPaths:
    """Paths needed per collected file that do not change in a session (rootdir resolved once, cache location, matcher)."""

    root: Path
    root_resolved: Path
    cache_dir: Path
    matcher: HyFileMatcher


def session_paths(config: pytest.Config) -> SessionPaths:
    """The session paths and matcher (prepared when first needed, kept in the stash)."""
    paths = config.stash.get(_SESSION_PATHS_KEY, None)
    if paths is None:
        root = Path(config.rootpath)
        paths = SessionPaths(
            root,
            root.resolve(),
            items_cache_dir(config),
            _matcher(str(root), tuple(_file_patterns(config))),
        )
        config.stash[_SESSION_PATHS_KEY] = paths
    return paths


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    """A collector for Hy files — executable ADR patterns and the ini doeff_hy_test_files patterns (test files).

    When test files were collected by ``pytest_collect_file`` in each package conftest.py, test_*.hy in a dir without a conftest
    was never collected, yet was counted in the daily population check (6 files in packages/doeff-hy/tests).
    The collection condition now lives in one place, the root ini, and the daily population check reads the same ini.
    """
    path = file_path
    if path.suffix != ".hy":
        return None
    if _should_collect_hy_file(path, parent.config):
        return DoeffAdrHyFile.from_parent(parent, path=path)
    if _is_hy_test_file(path, parent.config):
        return HyTestFile.from_parent(parent, path=path)
    return None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    """Swap an item collected from the record for the real module and function before fixture and skip evaluation (lazy import)."""
    parent = item.parent
    if isinstance(item, pytest.Function) and isinstance(parent, DoeffAdrHyFile):
        try:
            parent.realize(item)
        except RecordMismatch as exc:
            forget_cached(parent.path.resolve(), items_cache_dir(parent.config))
            pytest.fail(
                f"doeff-adr: record and real thing disagree — the record was deleted, so the next collection will import this file and rebuild it\n{exc}",
                pytrace=False,
            )
    return (yield)


def pytest_report_collectionfinish(config: pytest.Config) -> list[str]:
    """At the end of collection, report the number of files collected from the record and the files collected by import with the reason."""
    indexed = config.stash.get(_INDEXED_FILES_KEY, [])
    imported = config.stash.get(_IMPORTED_FILES_KEY, [])
    if not indexed and not imported:
        return []
    root = Path(config.rootpath)
    lines = [f"doeff-adr: collected from record: {len(indexed)} files; imported during collection: {len(imported)} files"]
    lines += [f"  import: {_relative_posix(path, root)} — {reason}" for path, reason in imported]
    checks = config.stash.get(_DEPENDENCY_CHECKS_KEY, None)
    if checks is not None:
        lines.append(checks.report())
    return lines


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    config.stash[_COLLECTED_FILES_KEY] = _resolved_item_files(items)


def _resolved_item_files(items: Iterable[pytest.Item]) -> frozenset[Path]:
    return frozenset(path.resolve() for path in {item.path for item in items})


def _write_pending(config: pytest.Config) -> None:
    """Write the records checked during collection in one go at the end of collection (outside any file's import span)."""
    pending: tuple[PendingEntry, ...] = config.stash.get(_PENDING_ENTRIES_KEY, ())
    config.stash[_PENDING_ENTRIES_KEY] = ()
    checks = config.stash.setdefault(_DEPENDENCY_CHECKS_KEY, DependencyChecks())
    root = session_paths(config).root_resolved
    cache_dir = items_cache_dir(config)
    for entry in pending:
        write_cached(entry, cache_dir, root, checks)


def pytest_collection_finish(session: pytest.Session) -> None:
    _write_pending(session.config)
    mode = _wiring_mode(session.config)
    if mode == "off":
        return
    verdict = session_wiring(session)
    if isinstance(verdict, WiringVerified):
        return
    message = wiring_failure_message(verdict, Path(session.config.rootpath), mode)
    if mode == "strict":
        raise pytest.UsageError(message)
    warnings.warn(pytest.PytestWarning(message), stacklevel=1)


def session_wiring(session: pytest.Session) -> WiringVerdict:
    """Wiring of the collection this session actually performed (measured once)."""
    config = session.config
    cached = config.stash.get(_WIRING_VERDICT_KEY, None)
    if cached is not None:
        return cached
    verdict = _measure_wiring(session)
    config.stash[_WIRING_VERDICT_KEY] = verdict
    return verdict


def default_scope_wiring(session: pytest.Session) -> WiringVerdict | NotDefaultScope:
    """Wiring of the configured default scope, read from the running session.

    The mouth for an in-session gate test: a default invocation (no path
    arguments) has already collected the default scope, so its own collection
    *is* the measurement — spawning a second ``pytest --collect-only`` from
    inside a test repeats O(suite) work under a per-test deadline. A session
    that collected anything else answers ``NotDefaultScope``.
    """
    config = session.config
    if not _collects_default_scope(config):
        return NotDefaultScope(tuple(str(arg) for arg in config.args))
    return session_wiring(session)


def wiring_failure_message(
    verdict: WiringUncollected | WiringWalkAborted,
    root: Path,
    mode: WiringMode = "strict",
) -> str:
    if isinstance(verdict, WiringUncollected):
        return _wiring_message(root, list(verdict.uncollected), mode)
    return _walk_budget_message(root, verdict.dirs_walked, verdict.max_dirs, mode)


class DoeffAdrHyFile(pytest.Module):
    """Collect Hy test files. Only module loading is swapped to the Hy loader; item generation is left to the pytest Module.

    Formerly functions were created directly one by one with ``pytest.Function.from_parent``, so the ``pytest.mark.parametrize`` that deftest ``:interpreters`` /
    ``:params`` attach was not expanded, and the parametrize fixtures ran only once with default values (a violation of ADR-DOE-HY-002 R2 "pass deftest params faithfully to fixtures";
    found 2026-09-25 in a doeff-claude-code test).
    Module collection expands parametrize into callspecs.

    No import during collection (#1223): for a file the record written by the item-producing macro can explain, a provisional module built from the record
    is given to Module collection, and the module import waits until item setup (``lazy_collection``).
    Only files the record cannot explain are imported during collection, as before.
    """

    _mut_real_module: types.ModuleType | None = None
    # Reason for importing during collection (file not in the cache) — the save right after import reads this. None for import at setup.
    _mut_import_reason: str | None = None
    _mut_indexed: Indexed | None = None
    _mut_functions: IndexedFunctions | None = None

    def _getobj(self) -> types.ModuleType:
        source = self.path.resolve()
        paths = session_paths(self.config)
        root = paths.root_resolved
        base = _import_base_for_path(source, root)
        module_name = _module_name_for_path(source, base)
        checks = self.config.stash.setdefault(_DEPENDENCY_CHECKS_KEY, DependencyChecks())
        plan = (
            plan_collection(source, paths.cache_dir, root, checks)
            if supports_indexed_collection(self)
            else NeedsImport("unknown collection hook — use normal import and collection")
        )
        match plan:
            case Indexed(records, fixtures):
                self._mut_indexed = plan
                self.config.stash.setdefault(_INDEXED_FILES_KEY, []).append(self.path)
                return stub_module(records, fixtures, source, module_name)
            case NeedsImport(reason):
                # Add before the import so that files whose import ends midway (e.g. module-level skip) are also reported.
                self.config.stash.setdefault(_IMPORTED_FILES_KEY, []).append((self.path, reason))
                self._mut_import_reason = reason
                module = self.config.hook.pytest_doeff_import_hy_module(collector=self)
                self._mut_real_module = module
                return module

    def collect(self) -> Iterable[pytest.Item | pytest.Collector]:
        """If listed in the skip list (ini doeff_hy_test_skips), one skip item with a reason without importing; otherwise per-function items.

        This is the only place that reads the list, and it applies to both the ADR collector (doeff_adr_hy_files) and the Hy test file collector (HyTestFile) —
        formerly only HyTestFile read it, so even an ADR book on the list was still collected and stayed red (2026-10-07).
        """
        skipped = _skipped_file_items(self)
        if skipped is not None:
            return skipped
        _ = self.obj  # _getobj decides between the record and a normal import.
        indexed = self._mut_indexed
        if indexed is None:
            return super().collect()
        self._mut_functions = IndexedFunctions(
            self,
            [record for record in indexed.records if isinstance(record, FunctionItem)],
            fixtures=indexed.fixtures,
        )
        return self._mut_functions.collect()

    def _genfunctions(self, name: str, funcobj: Callable[..., object]) -> Iterator[pytest.Function]:
        functions = self._mut_functions
        if functions is None:
            yield from super()._genfunctions(name, funcobj)
        else:
            yield from functions.generate(name, funcobj)

    def after_import(self, module: types.ModuleType) -> None:
        """Right after importing during collection, save if the record fully explains the real thing; otherwise add the reason to the report."""
        reason = self._mut_import_reason
        if reason is None:
            return
        self._mut_import_reason = None
        verified = verify_records(module, self._pytest_collects)
        # Fetch the record of macros used by expansion (the cross-check key) right after the import.
        expansion = expansion_of(module)
        problems = [
            *verified.problems,
            *([expansion.reason] if isinstance(expansion, ExpansionUnknown) else []),
        ]
        if problems or isinstance(expansion, ExpansionUnknown):
            imported = self.config.stash[_IMPORTED_FILES_KEY]
            imported[-1] = (self.path, f"{reason} — not saved: " + "; ".join(problems))
            return
        # Writing happens at the end of collection (_write_pending) — keeps record writing and reading loaded sources out of this file's import span.
        self.config.stash[_PENDING_ENTRIES_KEY] = self.config.stash.get(_PENDING_ENTRIES_KEY, ()) + (
            pending_entry(
                self.path.resolve(),
                module,
                verified.fixtures,
                verified.records,
                expansion,
                verified.dynamic,
            ),
        )

    def _pytest_collects(self, name: str) -> bool:
        """Whether pytest collects this name as a test (``python_functions`` / ``python_classes``)."""
        return self.funcnamefilter(name) or self.classnamefilter(name)

    def realize(self, item: pytest.Function) -> None:
        """Before item setup, import this file's module once and swap the item for the real function.

        If the real thing disagrees with the record, raise ``RecordMismatch`` (the caller turns the item red and deletes the record).
        """
        if self._mut_indexed is None:
            return
        real = self._mut_real_module
        if real is None:
            real = self.config.hook.pytest_doeff_import_hy_module(collector=self)
            recorded_names = [name for name, value in vars(self.obj).items() if callable(value)]
            check_no_unrecorded_items(recorded_names, real, self._pytest_collects, self.nodeid)
            swap_in_real_module_marks(self, self.obj, real)
            self._mut_real_module = real
        if item.obj is not getattr(real, item.originalname, None):
            swap_in_real_function(item, real)


@dataclass(frozen=True)
class HyTestSkip:
    """One line of ini doeff_hy_test_skips — a Hy test file reported as skip without importing, and its reason."""

    path: str
    reason: str


@dataclass(frozen=True)
class HyTestSkips:
    """All lines of the skip list (ini doeff_hy_test_skips) — the collector and the daily population check use this same read."""

    rows: tuple[HyTestSkip, ...]

    def reason_for(self, relative: str) -> str | None:
        """If the file at the path from rootdir is on the list, its reason (None if not) — to decide whether to skip it."""
        return next((row.reason for row in self.rows if row.path == relative), None)


def parse_hy_test_skips(lines: Sequence[str]) -> HyTestSkips:
    """Read the lines of doeff_hy_test_skips (`<path from rootdir> | <reason>`). A line missing its reason stops as a configuration error."""
    return HyTestSkips(tuple(_parse_hy_test_skip(line) for line in lines if line.strip()))


def _parse_hy_test_skip(line: str) -> HyTestSkip:
    """Split one line into path and reason — so an exclusion without a reason is not silently accepted."""
    path, separator, reason = line.partition("|")
    if not separator or not path.strip() or not reason.strip():
        raise pytest.UsageError(
            f"doeff_hy_test_skips  line must be of the form '<path from rootdir> | <reason>': {line!r}"
        )
    return HyTestSkip(path.strip(), reason.strip())


def _is_hy_test_file(path: Path, config: pytest.Config) -> bool:
    """Whether to collect this .hy as a Hy test file (ini doeff_hy_test_files — not collected if empty; the default for downstream repos)."""
    patterns = tuple(config.getini("doeff_hy_test_files"))
    if not patterns:
        return False
    return _matcher(str(session_paths(config).root), patterns).matches(path)


def _hy_test_skip_reason(path: Path, config: pytest.Config) -> str | None:
    """If the file is on the skip list (ini doeff_hy_test_skips), return its reason (None if not)."""
    relative = session_paths(config).matcher.relative_posix(str(path))
    return config.stash[_HY_TEST_SKIPS_KEY].reason_for(relative)


def _skipped_file_items(collector: DoeffAdrHyFile) -> list[pytest.Item] | None:
    """If the file is on the skip list, one skip item with a reason without importing (shown in the run-count report every time).
    Otherwise None."""
    reason = _hy_test_skip_reason(collector.path, collector.config)
    if reason is None:
        return None
    skipped = HyScriptItem.from_parent(collector, name=HyScriptItem.NAME)
    skipped.add_marker(pytest.mark.skip(reason=reason))
    return [skipped]


class HyTestFile(DoeffAdrHyFile):
    """A Hy test file matching ini doeff_hy_test_files.

    A file with deftest (or test_ functions) becomes per-function items like ``DoeffAdrHyFile``; a "script run top to bottom" without them
    becomes one item per file (``HyScriptItem`` — green if running raises no exception, red if it does).
    Which form applies is decided by the result of reading the file (whether 0 items were collected), not by the look of names or contents — deciding by the look
    would mistake a file that builds tests with another macro wrapping deftest for a script and turn it green without running a single test.

    A file on ini doeff_hy_test_skips is not imported and becomes one skip item with a reason (read in one place, the parent
    ``DoeffAdrHyFile.collect``).
    """

    def collect(self) -> Iterable[pytest.Item | pytest.Collector]:
        """One skip item if on the skip list (parent) · the items if it defines tests · one script item if it defines none."""
        collected = list(super().collect())
        return collected or [HyScriptItem.from_parent(self, name=HyScriptItem.NAME)]


class HyScriptItem(pytest.Item):
    """An item that runs a Hy test file defining no tests (a script run top to bottom) as one test.

    Running the file is the module import itself. When collected from the record (cache), this item's run is the first import.
    When imported during collection without a record, it has already run there (on an exception the file's collection is already red at that point).
    """

    NAME = "script"

    def runtest(self) -> None:
        """Run the file top to bottom (= import the module) — an exception turns this test red."""
        parent = self.parent
        if not isinstance(parent, HyTestFile):
            raise TypeError(
                f"HyScriptItem parent must be a HyTestFile: {type(parent).__name__}"
            )
        if parent._mut_real_module is not None:
            return
        real = self.config.hook.pytest_doeff_import_hy_module(collector=parent)
        # The record said "a file without tests" but the real thing defines tests, so the record is stale — do not silently turn it green as a script.
        check_no_unrecorded_items([], real, parent._pytest_collects, parent.nodeid)
        parent._mut_real_module = real

    def reportinfo(self) -> tuple[Path, int, str]:
        """Show the file name in report lines (pytest Item contract — skip reports need a line number, so 0 at the file head)."""
        return self.path, 0, f"{self.path.name} (Hy script)"


def items_cache_dir(config: pytest.Config) -> Path:
    """Location of the item record cache — order and rationale are in item_cache.items_cache_dir_for (ini → environment variable → user cache dir)."""
    return items_cache_dir_for(str(config.getini("doeff_adr_items_cache")), Path(config.rootpath))


def _should_collect_hy_file(path: Path, config: pytest.Config) -> bool:
    return session_paths(config).matcher.matches(path)


def _file_patterns(config: pytest.Config) -> list[str]:
    return [*DEFAULT_FILE_PATTERNS, *config.getini("doeff_adr_hy_files")]


@functools.cache
def _pattern_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """Combine a sequence of patterns into one regex (once per sequence).

    Collection and the wiring scan match per file, so calling fnmatch per pattern per candidate path made nearly
    60,000 calls in one collection. The meaning equals fnmatch.fnmatch (apply normcase, match the whole).
    """
    return re.compile("|".join(f"(?:{fnmatch.translate(os.path.normcase(p))})" for p in patterns))


def _matches_file_patterns(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """Whether any of the file name, path from rootdir, or absolute path matches the executable ADR patterns."""
    regex = _pattern_regex(tuple(patterns))
    candidates = (path.name, _relative_posix(path, root), path.as_posix())
    return any(regex.match(os.path.normcase(candidate)) for candidate in candidates)


def _wiring_mode(config: pytest.Config) -> WiringMode:
    command_line_mode = config.getoption("doeff_adr_wiring")
    configured_mode = command_line_mode or config.getini("doeff_adr_wiring")
    if configured_mode == "off":
        return "off"
    if configured_mode == "warn":
        return "warn"
    if configured_mode == "strict":
        return "strict"
    choices = ", ".join(sorted(WIRING_MODES))
    raise pytest.UsageError(f"doeff_adr_wiring must be one of {choices}; got {configured_mode!r}")


def _wiring_max_dirs(config: pytest.Config) -> int:
    """Directory budget for the wiring walk. Positive integers only.

    An unparsable or non-positive value silently becoming "unlimited" would
    reopen the unbounded-walk hole, so the vocabulary is closed: the intentional
    opt-out spelling stays ``doeff_adr_wiring=off``.
    """
    raw = config.getini("doeff_adr_wiring_max_dirs")
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise pytest.UsageError(
            f"doeff_adr_wiring_max_dirs must be a positive integer; got {raw!r} "
            "(use doeff_adr_wiring=off for an intentional opt-out)"
        ) from None
    if value <= 0:
        raise pytest.UsageError(
            f"doeff_adr_wiring_max_dirs must be a positive integer; got {raw!r} "
            "(use doeff_adr_wiring=off for an intentional opt-out)"
        )
    return value


def _collects_default_scope(config: pytest.Config) -> bool:
    source = config.args_source
    if source is pytest.Config.ArgsSource.TESTPATHS:
        return True
    if source is pytest.Config.ArgsSource.INVOCATION_DIR:
        # No testpaths configured: the default scope is rootdir itself, and only
        # an invocation from rootdir collects all of it.
        return Path(config.invocation_params.dir) == Path(config.rootpath)
    return False


def _measure_wiring(session: pytest.Session) -> WiringVerdict:
    config = session.config
    max_dirs = _wiring_max_dirs(config)
    paths = session_paths(config)
    try:
        executable_adrs = _discover_executable_adrs(
            paths.root,
            _file_patterns(config),
            _norecurse_dir_patterns(config),
            max_dirs=max_dirs,
            matcher=paths.matcher,
            root_resolved=paths.root_resolved,
        )
    except WiringWalkBudgetError as exc:
        return WiringWalkAborted(dirs_walked=exc.dirs_walked, max_dirs=max_dirs)
    return _wiring_verdict(executable_adrs, _collected_files(session))


def _collected_files(session: pytest.Session) -> frozenset[Path]:
    snapshot = session.config.stash.get(_COLLECTED_FILES_KEY, None)
    if snapshot is not None:
        return snapshot
    return _resolved_item_files(session.items)


def _wiring_verdict(
    executable_adrs: set[Path], collected_files: frozenset[Path]
) -> WiringVerified | WiringUncollected:
    uncollected = tuple(sorted(executable_adrs - collected_files))
    if uncollected:
        return WiringUncollected(uncollected)
    return WiringVerified(frozenset(executable_adrs))


def _norecurse_dir_patterns(config: pytest.Config) -> list[str]:
    """Directory-name globs pytest itself refuses to collect into (norecursedirs).

    Wiring discovery must stay consistent with what pytest collection *could*
    reach: a defadr file inside a norecursedirs-matched directory (default
    includes ``.*`` — e.g. ``.claude/worktrees`` checkout copies) can never be
    collected, so reporting it as mis-wired is a false positive by construction.
    """
    return list(config.getini("norecursedirs"))


def _is_directory(entry: os.DirEntry[str]) -> bool:
    """Same distinction as os.walk: follow symlinks and treat as dir if dir (anything unfollowable or unreadable is on the file side)."""
    try:
        return entry.is_dir()
    except OSError:
        return False


def _discover_executable_adrs(
    root: Path,
    patterns: Sequence[str],
    norecurse: Sequence[str] = (),
    max_dirs: int = DEFAULT_WIRING_MAX_DIRS,
    matcher: HyFileMatcher | None = None,
    root_resolved: Path | None = None,
) -> set[Path]:
    """Collect all executable ADR files (resolved paths) under rootdir.

    Walks like os.walk with scandir (do not descend into symlinked dirs · skip unreadable dirs · cap on dir count), and drops names not ending in ``.hy``
    before building a Path (one scan used to build Paths for tens of thousands of files). The resolved path of a file under rootdir
    is built from the once-resolved rootdir (descended dirs contain no symlinks). A file that is itself a symlink
    is resolved as before.
    """
    matcher = _matcher(str(root), tuple(patterns)) if matcher is None else matcher
    prune = _pattern_regex(tuple(norecurse)) if norecurse else None
    root_resolved = root.resolve() if root_resolved is None else root_resolved
    executable_adrs: set[Path] = set()
    pending: list[str] = [str(root)]
    dirs_walked = 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as scan:
                entries = list(scan)
        except OSError:
            continue
        dirs_walked += 1
        if dirs_walked > max_dirs:
            raise WiringWalkBudgetError(dirs_walked)
        for entry in sorted(entries, key=lambda entry: entry.name, reverse=True):
            name = entry.name
            if _is_directory(entry):
                if name in IGNORED_DISCOVERY_DIRECTORIES or (
                    prune is not None and prune.match(os.path.normcase(name))
                ):
                    continue
                if not entry.is_symlink():
                    pending.append(entry.path)
                continue
            if not name.endswith(".hy"):
                continue
            text = entry.path
            if not matcher.matches_text(text, name):
                continue
            if entry.is_symlink() or not text.startswith(matcher.root_prefix):
                executable_adrs.add(Path(text).resolve())
            else:
                executable_adrs.add(root_resolved / text[len(matcher.root_prefix) :])
    return executable_adrs


def _wiring_message(root: Path, paths: list[Path], mode: WiringMode) -> str:
    outcome = "failed" if mode == "strict" else "warning"
    rendered_paths = "\n".join(f"  - {_relative_posix(path, root)}" for path in paths)
    return (
        f"doeff-adr wiring verification {outcome}: executable ADR files exist but were not "
        f"collected:\n{rendered_paths}\n"
        "Add their directories to pytest testpaths or the CI pytest arguments. "
        "Use doeff_adr_wiring=off only for an intentional opt-out."
    )


def _walk_budget_message(root: Path, dirs_walked: int, max_dirs: int, mode: WiringMode) -> str:
    outcome = "failed" if mode == "strict" else "warning"
    return (
        f"doeff-adr wiring verification {outcome}: aborted after walking {dirs_walked} "
        f"directories under rootdir {root} (budget: doeff_adr_wiring_max_dirs = {max_dirs}). "
        "The verification walk covers the whole rootdir; a rootdir this broad usually means no "
        "ini file anchors the project and pytest resolved rootdir far above it (e.g. the home "
        "directory), which makes every run silently crawl the filesystem. Fix: anchor rootdir "
        "with a pytest.ini/pyproject.toml near the executable ADRs, raise "
        "doeff_adr_wiring_max_dirs, or set doeff_adr_wiring=off for an intentional opt-out."
    )


def _relative_posix(path: Path, root: Path) -> str:
    """Path from rootdir (for matching and reporting).

    Collection and the wiring scan pass paths under rootdir one file at a time, so if textually under rootdir it is made
    relative as is. Symlink resolution (realpath) only happens when outside — resolving every time was called nearly 10,000 times in one
    collection and took 10% of collection time.
    """
    path_text = path.as_posix()
    prefix = root.as_posix().rstrip("/") + "/"
    if path_text.startswith(prefix):
        return path_text[len(prefix) :]
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _import_hy_file(path: Path, root: Path) -> types.ModuleType:
    path = path.resolve()
    root = _import_base_for_path(path, root)
    module_name = _module_name_for_path(path, root)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    _ensure_macro_module_loaded()
    return import_module_for(path, module_name)


def _ensure_macro_module_loaded() -> None:
    if "doeff_adr.macros" in sys.modules:
        return
    path = Path(__file__).with_name("macros.hy").resolve()
    loader = HyLoader("doeff_adr.macros", str(path))
    spec = importlib.util.spec_from_file_location(
        "doeff_adr.macros",
        path,
        loader=loader,
    )
    if spec is None:
        raise ImportError(f"could not create import spec for doeff_adr macros: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["doeff_adr.macros"] = module
    loader.exec_module(module)


def _relative_module_parts(path: Path, root: Path) -> list[str] | None:
    """The part of the path (without extension) relative to rootdir (the base of the module name). None if not under rootdir.

    Collection decides the import base dir and module name per file, so this part is computed twice. When textually under rootdir
    it is split by slicing the string — ``with_suffix`` / ``relative_to`` rebuild the path and were
    prominent in collecting 420 files. When the string prefix does not match, fall back to the previous part comparison.
    """
    text = path.as_posix()
    root_text = root.as_posix()
    prefix = root_text if root_text.endswith("/") else root_text + "/"
    if text.startswith(prefix):
        relative = text[len(prefix) :]
        suffix = path.suffix
        return (relative[: -len(suffix)] if suffix else relative).split("/")
    try:
        return list(path.with_suffix("").relative_to(root).parts)
    except ValueError:
        return None


def _import_base_for_path(path: Path, root: Path) -> Path:
    """The directory an executable Hy file is imported relative to.

    The rootdir, as long as every rootdir-relative part of the path is an
    identifier (the long-standing rule: ``controllers/kanban/tests/test_x.hy``
    becomes ``controllers.kanban.tests.test_x``). A workspace package directory
    such as ``packages/doeff-cluster`` cannot be part of a module name, so for
    those paths the base falls back to pytest's own ``prepend`` rule: the first
    ancestor that is not a package (has neither ``__init__.py`` nor
    ``__init__.hy``). ``packages/doeff-cluster/tests/test_x.hy`` with a
    ``tests/__init__.py`` is then imported as ``tests.test_x`` from
    ``packages/doeff-cluster``. Paths outside the rootdir keep the rootdir so
    ``_module_name_for_path`` reports them.
    """
    parts = _relative_module_parts(path, root)
    if parts is None or all(part.isidentifier() for part in parts):
        return root
    base = path.parent
    while (base / "__init__.py").exists() or (base / "__init__.hy").exists():
        base = base.parent
    return base


def _module_name_for_path(path: Path, root: Path) -> str:
    parts = _relative_module_parts(path, root)
    if parts is None:
        raise ValueError(f"executable ADR file is outside pytest root: {path}")
    bad_parts = [part for part in parts if not part.isidentifier()]
    if bad_parts:
        raise ValueError(
            "executable ADR Hy files must have importable module path parts: "
            f"{path} contains {bad_parts!r}"
        )
    return ".".join(parts)
