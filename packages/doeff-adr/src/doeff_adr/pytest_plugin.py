"""Pytest plugin for executable ADR Hy files, and for the wiring of the canonical gate.

Wiring verification owns two file kinds, because the hole is the same one
twice: a file that exists, looks like a check, and is never reached by the
default ``pytest`` invocation. Kind one is the executable ADR (``defadr_*.hy``)
this plugin also collects; kind two is the ordinary Python test file, which
pytest collects on its own — as long as somebody put its directory in
``testpaths``. Nobody did, for 19 of doeff's 27 package test trees
(2026-09-19: 3,200 tests silent, five files rotted against APIs deleted
months earlier). One measurement answers for both kinds.
"""

import fnmatch
import importlib
import importlib.util
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from hy.importer import HyLoader

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
# One line per uncollected file is the size of the repository once the second
# file kind joined the walk: doeff measured 405 path lines (378 .py + 27 .hy) on
# every focused ``pytest tests/test_one.py`` (2026-09-19). What folds is the
# *drawing*, never the verdict — and only warn's: strict is the CI mouth and
# owes every path it refuses on (law strict-wiring-fails-closed).
WIRING_WARN_PATHS_SHOWN = 5


class WiringWalkBudgetError(Exception):
    """Wiring discovery visited more directories than the configured budget."""

    def __init__(self, dirs_walked: int) -> None:
        super().__init__(str(dirs_walked))
        self.dirs_walked = dirs_walked


@dataclass(frozen=True)
class WiringVerified:
    """Every file the gate owns under rootdir was reached by the session's collection."""

    wired_files: frozenset[Path]


@dataclass(frozen=True)
class WiringUncollected:
    """Files the gate owns exist that the session's collection did not reach."""

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
    direction: a narrower one misses files the default scope reaches, and a wider
    one (``pytest tests docs/adr`` while testpaths lacks docs/adr) reaches files
    the default scope would leave silent.
    """

    args: tuple[str, ...]


WiringVerdict = WiringVerified | WiringUncollected | WiringWalkAborted

# Wiring is a property of the collection *scope*, not of the selection: -k / -m /
# --deselect drop items after collection, and a file they deselect was still
# reached (the canonical doeff gate itself runs with ``-m 'not e2e'``). The files
# are therefore snapshotted before any deselection hook runs.
_COLLECTED_FILES_KEY = pytest.StashKey[frozenset[Path]]()
# Every file the collection *reached*, whether or not items came out of it.
# Item count is the wrong measure for the same reason selection is: a module
# with a top-level ``pytest.importorskip`` yields zero items wherever the
# optional dependency is missing, so an item-based verdict would call a
# correctly wired tree mis-wired on one machine and not another.
_REACHED_FILES_KEY = pytest.StashKey[set[Path]]()
# One measurement per session: the collection-finish report and an in-session
# gate test read the same verdict instead of walking rootdir twice.
_WIRING_VERDICT_KEY = pytest.StashKey[WiringVerdict]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addini(
        "doeff_adr_hy_files",
        "Glob patterns for executable ADR Hy files collected by doeff-adr.",
        type="linelist",
        default=[],
    )
    parser.addini(
        "doeff_adr_wiring_exclude",
        "Path globs (rootdir-relative, posix) for files the canonical gate must NOT collect: "
        "sample inputs another tool feeds through its own suite, example scripts that run at "
        "import, and trees declared dark on purpose. Write the reason next to each entry.",
        type="linelist",
        default=[],
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


def pytest_collect_file(file_path: Any, parent: pytest.Collector) -> pytest.Collector | None:
    path = _coerce_path(file_path)
    if path.suffix != ".hy":
        return None
    if not _should_collect_hy_file(path, parent.config):
        return None
    return DoeffAdrHyFile.from_parent(parent, path=path)


def pytest_collectstart(collector: pytest.Collector) -> None:
    if not isinstance(collector, pytest.File):
        return
    reached = collector.config.stash.setdefault(_REACHED_FILES_KEY, set())
    reached.add(Path(collector.path).resolve())


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    config.stash[_COLLECTED_FILES_KEY] = frozenset(Path(item.path).resolve() for item in items)


def pytest_collection_finish(session: pytest.Session) -> None:
    mode = _wiring_mode(session.config)
    if mode == "off":
        return
    verdict = session_wiring(session)
    if isinstance(verdict, WiringVerified):
        return
    message = wiring_failure_message(
        verdict,
        Path(session.config.rootpath),
        mode,
        default_scope=_collects_default_scope(session.config),
    )
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
    *,
    default_scope: bool = True,
) -> str:
    if isinstance(verdict, WiringUncollected):
        return _wiring_message(root, list(verdict.uncollected), mode, default_scope=default_scope)
    return _walk_budget_message(root, verdict.dirs_walked, verdict.max_dirs, mode)


class DoeffAdrHyFile(pytest.File):
    def collect(self) -> Any:
        module = _import_hy_file(self.path, self.config.rootpath)
        for name in sorted(attr for attr in dir(module) if attr.startswith("test_")):
            callobj = getattr(module, name)
            if callable(callobj):
                yield pytest.Function.from_parent(self, name=name, callobj=callobj)


def _coerce_path(path: Any) -> Path:
    if isinstance(path, Path):
        return path
    strpath = getattr(path, "strpath", None)
    if strpath is not None:
        return Path(strpath)
    return Path(str(path))


def _should_collect_hy_file(path: Path, config: pytest.Config) -> bool:
    root = Path(config.rootpath)
    patterns = _file_patterns(config)
    return _matches_file_patterns(path, root, patterns)


def _file_patterns(config: pytest.Config) -> tuple[str, ...]:
    return (*DEFAULT_FILE_PATTERNS, *config.getini("doeff_adr_hy_files"))


def _python_file_patterns(config: pytest.Config) -> tuple[str, ...]:
    """pytest's own ``python_files``. The gate holds no second opinion about it.

    A project that renamed the pattern would otherwise get both false reds (a
    ``test_*.py`` pytest never collects here) and false greens (its actual
    tests, invisible to a hardcoded guess).
    """
    return tuple(config.getini("python_files"))


def _exclude_patterns(config: pytest.Config) -> tuple[str, ...]:
    return tuple(config.getini("doeff_adr_wiring_exclude"))


def _matches_file_patterns(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    rel = _relative_posix(path, root)
    candidates = {path.name, rel, path.as_posix()}
    return any(
        fnmatch.fnmatch(candidate, pattern) for pattern in patterns for candidate in candidates
    )


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
    try:
        wired_files = _discover_wired_files(
            Path(config.rootpath),
            _file_patterns(config),
            _python_file_patterns(config),
            _norecurse_dir_patterns(config),
            _exclude_patterns(config),
            max_dirs=max_dirs,
        )
    except WiringWalkBudgetError as exc:
        return WiringWalkAborted(dirs_walked=exc.dirs_walked, max_dirs=max_dirs)
    return _wiring_verdict(wired_files, _collected_files(session))


def _collected_files(session: pytest.Session) -> frozenset[Path]:
    reached = session.config.stash.get(_REACHED_FILES_KEY, set())
    snapshot = session.config.stash.get(_COLLECTED_FILES_KEY, None)
    if snapshot is None:
        snapshot = frozenset(Path(item.path).resolve() for item in session.items)
    return frozenset(reached | set(snapshot))


def _wiring_verdict(
    wired_files: set[Path], collected_files: frozenset[Path]
) -> WiringVerified | WiringUncollected:
    uncollected = tuple(sorted(wired_files - collected_files))
    if uncollected:
        return WiringUncollected(uncollected)
    return WiringVerified(frozenset(wired_files))


def _norecurse_dir_patterns(config: pytest.Config) -> tuple[str, ...]:
    """Directory-name globs pytest itself refuses to collect into (norecursedirs).

    Wiring discovery must stay consistent with what pytest collection *could*
    reach: a file inside a norecursedirs-matched directory (default includes
    ``.*`` — e.g. ``.claude/worktrees`` checkout copies) can never be collected,
    so reporting it as mis-wired is a false positive by construction.
    """
    return tuple(config.getini("norecursedirs"))


def _discover_wired_files(
    root: Path,
    adr_patterns: tuple[str, ...],
    python_patterns: tuple[str, ...] = (),
    norecurse: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    max_dirs: int = DEFAULT_WIRING_MAX_DIRS,
) -> set[Path]:
    wired_files: set[Path] = set()
    for dirs_walked, (directory, directory_names, file_names) in enumerate(
        os.walk(root), start=1
    ):
        if dirs_walked > max_dirs:
            raise WiringWalkBudgetError(dirs_walked)
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name not in IGNORED_DISCOVERY_DIRECTORIES
            and not any(fnmatch.fnmatch(name, pattern) for pattern in norecurse)
        )
        for file_name in sorted(file_names):
            path = Path(directory, file_name)
            if not _is_gate_owned_file(path, root, adr_patterns, python_patterns):
                continue
            if _is_declared_uncollectable(path, root, exclude):
                continue
            wired_files.add(path.resolve())
    return wired_files


def _is_gate_owned_file(
    path: Path,
    root: Path,
    adr_patterns: tuple[str, ...],
    python_patterns: tuple[str, ...],
) -> bool:
    """The two kinds the canonical gate answers for: executable ADRs and Python tests."""
    if path.suffix == ".hy":
        return _matches_file_patterns(path, root, adr_patterns)
    if path.suffix == ".py":
        return any(fnmatch.fnmatch(path.name, pattern) for pattern in python_patterns)
    return False


def _is_declared_uncollectable(path: Path, root: Path, exclude: tuple[str, ...]) -> bool:
    """A file the project declared the gate must not expect to be collected.

    The escape hatch is deliberately per-path and written in the project's own
    ini, so silencing the gate leaves a reviewable line behind — unlike
    ``doeff_adr_wiring=off``, which silences everything at once.
    """
    rel = _relative_posix(path, root)
    return any(fnmatch.fnmatch(rel, pattern) for pattern in exclude)


def _wiring_message(
    root: Path,
    paths: list[Path],
    mode: WiringMode,
    *,
    default_scope: bool = True,
) -> str:
    outcome = "failed" if mode == "strict" else "warning"
    shown = paths if mode == "strict" else paths[:WIRING_WARN_PATHS_SHOWN]
    rendered_paths = "\n".join(f"  - {_relative_posix(path, root)}" for path in shown)
    hidden = len(paths) - len(shown)
    if hidden:
        rendered_paths += (
            f"\n  ... and {hidden} more; see them all with --doeff-adr-wiring=strict "
            "or `doeff-adr verify-wiring`"
        )
    # A session that named its own paths legitimately leaves the rest of the
    # repository uncollected, so its list is long for a reason that is not a
    # wiring defect. It still reports: R1's own counterexample is a named-path
    # CI command ("CI が tests だけを明示して docs/adr を走査しない"), which
    # silence would blind. Saying why costs one clause.
    scope_note = (
        ""
        if default_scope
        else " This session collected the paths it was given, so everything outside them is "
        "in that list; the gate speaks for the default invocation (no path arguments)."
    )
    noun = "file" if len(paths) == 1 else "files"
    return (
        f"doeff-adr wiring verification {outcome}: {len(paths)} {noun} the canonical pytest "
        f"gate must collect (executable ADRs and Python test files) exist but were not "
        f"collected:\n"
        f"{rendered_paths}\n"
        "Add their directories to pytest testpaths or the CI pytest arguments. A file that "
        "can never be collected (a sample input for another tool, a script that runs at "
        "import) belongs in doeff_adr_wiring_exclude with its reason. "
        f"Use doeff_adr_wiring=off only for an intentional opt-out.{scope_note}"
    )


def _walk_budget_message(root: Path, dirs_walked: int, max_dirs: int, mode: WiringMode) -> str:
    outcome = "failed" if mode == "strict" else "warning"
    return (
        f"doeff-adr wiring verification {outcome}: aborted after walking {dirs_walked} "
        f"directories under rootdir {root} (budget: doeff_adr_wiring_max_dirs = {max_dirs}). "
        "The verification walk covers the whole rootdir; a rootdir this broad usually means no "
        "ini file anchors the project and pytest resolved rootdir far above it (e.g. the home "
        "directory), which makes every run silently crawl the filesystem. Fix: anchor rootdir "
        "with a pytest.ini/pyproject.toml near the tests, raise "
        "doeff_adr_wiring_max_dirs, or set doeff_adr_wiring=off for an intentional opt-out."
    )


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _import_hy_file(path: Path, root: Path) -> Any:
    root = root.resolve()
    path = path.resolve()
    module_name = _module_name_for_path(path, root)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    _ensure_macro_module_loaded()
    existing = sys.modules.get(module_name)
    if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == path:
        return existing
    importlib.invalidate_caches()
    loader = HyLoader(module_name, str(path))
    spec = importlib.util.spec_from_file_location(module_name, path, loader=loader)
    if spec is None:
        raise ImportError(f"could not create import spec for executable ADR: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


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


def _module_name_for_path(path: Path, root: Path) -> str:
    try:
        relative = path.with_suffix("").relative_to(root)
    except ValueError as exc:
        raise ValueError(f"executable ADR file is outside pytest root: {path}") from exc
    parts = relative.parts
    bad_parts = [part for part in parts if not part.isidentifier()]
    if bad_parts:
        raise ValueError(
            "executable ADR Hy files must have importable module path parts: "
            f"{path} contains {bad_parts!r}"
        )
    return ".".join(parts)
