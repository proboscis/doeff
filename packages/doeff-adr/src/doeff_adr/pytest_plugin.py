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
from collections.abc import Generator, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from hy.importer import HyLoader

from doeff_adr.item_cache import DEFAULT_CACHE_DIR, forget_cached, write_cached
from doeff_adr.lazy_collection import (
    CollectionPlan,
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
from doeff_adr.recorded_collection import (
    RecordedModuleCollection,
    collect_recorded,
    generate_functions,
    known_collection_hooks,
)
from doeff_adr.source_dependencies import DependencyChecks

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
# 収集の終わりの報告(agora-redesign #1223): 記録から収集した file と、import して収集した file とその理由。
_INDEXED_FILES_KEY = pytest.StashKey[list[Path]]()
_IMPORTED_FILES_KEY = pytest.StashKey[list[tuple[Path, str]]]()
_DEPENDENCY_CHECKS_KEY = pytest.StashKey[DependencyChecks]()
# 記録から集めた file のうち、未知の plugin の hook が収集に加わるので pytest の Module の汎用の収集に回した file
# (agora-redesign #1551 — 記録の関数の名から item を作る近道を使わなかった物。報告に出して経路を見えるようにする)。
_GENERIC_FILES_KEY = pytest.StashKey[list[Path]]()


class DoeffAdrHookspecs:
    """doeff-adr が他の plugin に見せる hook。"""

    @pytest.hookspec(firstresult=True)
    def pytest_doeff_import_hy_module(self, collector: pytest.Module) -> types.ModuleType | None:
        """Hy の test file の module を読む — 収集で読む時(記録で説明できない file)と、item の setup で読む時
        (記録から収集した file)の両方がここを通る。import の時間を測る plugin(doeff-hy-pytest の上限)は、この hook を
        wrapper で包む(agora-redesign #1225)。"""
        raise NotImplementedError


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    """doeff-adr の hook を pytest に登録する。"""
    pluginmanager.add_hookspecs(DoeffAdrHookspecs)


@pytest.hookimpl
def pytest_doeff_import_hy_module(collector: pytest.Module) -> types.ModuleType:
    """Hy の test file の module を読む既定の実装。収集の中で読んだ file は、読んだ直後に記録を実物と突き合わせて
    キャッシュに保存する(時間の上限の wrapper が後で落としても、読めた module の記録は残す — 判定と保存は別の事柄)。"""
    module = _import_hy_file(collector.path, Path(collector.config.rootpath))
    if isinstance(collector, DoeffAdrHyFile):
        collector.after_import(module)
    return module


class HySourceFinder(importlib.abc.MetaPathFinder):
    """.hy の module を pytest の assert の書き換えに渡さないための import の探し手。

    pytest の書き換え(``AssertionRewritingHook``)は、命令の行で名指した file の module を ``SourceFileLoader`` の
    spec で見つけると ``ast.parse`` で Python として読み直す。Hy の loader は ``SourceFileLoader`` の子なので、名指しの
    .hy を別の test file が名前で import すると SyntaxError になる(記録から収集した file はまだ読まれていないので
    当たりやすい — agora-redesign #1211 の後の報告)。書き換えより前に置き、.hy の spec はそのまま(Hy の loader)返す。
    loader そのものには触れない(bytecode の見張り〔#1292〕が包む ``SourceFileLoader`` の口はそのまま効く)。
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
    """.hy の探し手を import の探し手の先頭に置く — pytest は書き換えの探し手を plugin の configure より前に置くので、
    ここで先頭に入れれば書き換えより先に .hy を引き受けられる。"""
    if _HY_SOURCE_FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _HY_SOURCE_FINDER)


def pytest_unconfigure(config: pytest.Config) -> None:
    """configure で置いた .hy の探し手を外す(同じ process で pytest を何度も走らせる時に積み重ねないため)。"""
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
        "doeff_adr_items_cache",
        "Directory of the doeff-adr cache of Hy test item records (empty = ~/.cache/doeff-adr/pytest-items).",
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


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    path = file_path
    if path.suffix != ".hy":
        return None
    if not _should_collect_hy_file(path, parent.config):
        return None
    return DoeffAdrHyFile.from_parent(parent, path=path)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    """記録から収集した item は、fixture と skip の評価より先に実物の module と関数に替える(遅延 import)。"""
    parent = item.parent
    if isinstance(item, pytest.Function) and isinstance(parent, DoeffAdrHyFile):
        try:
            parent.realize(item)
        except RecordMismatch as exc:
            forget_cached(parent.path.resolve(), items_cache_dir(parent.config))
            pytest.fail(
                f"doeff-adr: 記録と実物が食い違った — 記録を消したので、次の収集はこの file を import して作り直す\n{exc}",
                pytrace=False,
            )
    return (yield)


def pytest_report_collectionfinish(config: pytest.Config) -> list[str]:
    """収集の終わりに、記録から収集した file の数と、import して収集した file とその理由を報告する。"""
    indexed = config.stash.get(_INDEXED_FILES_KEY, [])
    imported = config.stash.get(_IMPORTED_FILES_KEY, [])
    if not indexed and not imported:
        return []
    root = Path(config.rootpath)
    lines = [f"doeff-adr: 記録から収集 {len(indexed)} file・収集で import {len(imported)} file"]
    lines += [f"  import: {_relative_posix(path, root)} — {reason}" for path, reason in imported]
    generic = config.stash.get(_GENERIC_FILES_KEY, [])
    if generic:
        lines.append(f"doeff-adr: 記録から収集した file のうち、未知の plugin の hook があり pytest の汎用の収集に回した {len(generic)} file")
        lines += [f"  generic: {_relative_posix(path, root)}" for path in generic]
    checks = config.stash.get(_DEPENDENCY_CHECKS_KEY, None)
    if checks is not None:
        lines.append(checks.report())
    return lines


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    config.stash[_COLLECTED_FILES_KEY] = _resolved_item_files(items)


def _resolved_item_files(items: Sequence[pytest.Item]) -> frozenset[Path]:
    """item の file の実の path の集まり。symlink の解決(realpath)は file ごとに 1 回 — item ごとに解くと、
    1 file に 7 本ほどの item が並ぶので同じ解決を繰り返していた(agora-redesign #1551)。"""
    return frozenset(path.resolve() for path in {item.path for item in items})


def pytest_collection_finish(session: pytest.Session) -> None:
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
    """Hy の file の検の収集。module の取り込みだけを Hy の loader に替え、項目の生成は pytest の Module に任せる。

    以前は ``pytest.Function.from_parent`` で関数を 1 つずつ直に作っていたので、deftest の ``:interpreters`` /
    ``:params`` が付ける ``pytest.mark.parametrize`` が展開されず、parametrize の fixture は既定の値のまま 1 本だけ
    走っていた(ADR-DOE-HY-002 R2「deftest の params を fixture へ忠実に受け渡す」の違反・2026-09-25 の
    doeff-claude-code の検で発覚)。Module の収集は parametrize を callspec に展開する。

    収集で import しない(agora-redesign #1211 / #1223): item を作る macro が書いた記録で説明できる file は、記録から
    作った仮の module を Module の収集に渡し、module の import は item の setup まで待つ(``lazy_collection``)。
    記録で説明できない file だけ、今までどおり収集で import する。

    記録で説明できる file の item は、記録の関数の名から作る(``recorded_collection`` — agora-redesign #1551)。
    収集に未知の plugin の hook が加わる時は、仮の module を pytest の Module の収集にそのまま渡す。
    """

    _mut_real_module: types.ModuleType | None = None
    # 収集の中で import する理由(キャッシュに無い file)— import の直後の保存がこれを見る。setup の import では None。
    _mut_import_reason: str | None = None
    # この file をどう収集するか(_getobj が決める — 記録から / import して)。
    _mut_plan: CollectionPlan | None = None
    # 記録から item を作っている ``collect`` の間だけの状態(_genfunctions がこれを見る)。
    _mut_recorded: RecordedModuleCollection | None = None

    def _getobj(self) -> types.ModuleType:
        # source と rootdir の実の path は 1 回ずつ解く(symlink の解決は重い — agora-redesign #1551)。
        source = self.path.resolve()
        root = Path(self.config.rootpath).resolve()
        module_name = _module_name_for_path(source, _import_base_for_path(source, root))
        checks = self.config.stash.setdefault(_DEPENDENCY_CHECKS_KEY, DependencyChecks())
        plan = plan_collection(source, items_cache_dir(self.config), root, checks)
        self._mut_plan = plan
        match plan:
            case Indexed(records, fixtures):
                self.config.stash.setdefault(_INDEXED_FILES_KEY, []).append(self.path)
                return stub_module(records, fixtures, source, module_name)
            case NeedsImport(reason):
                # import が途中で終わる file(module ごと skip する等)も報告に載せるため、import の前に積む。
                self.config.stash.setdefault(_IMPORTED_FILES_KEY, []).append((self.path, reason))
                self._mut_import_reason = reason
                module = self.config.hook.pytest_doeff_import_hy_module(collector=self)
                self._mut_real_module = module
                return module

    def collect(self) -> Iterable[pytest.Item | pytest.Collector]:
        """記録で説明できる file は記録の関数の名から item を作り、それ以外は pytest の Module の収集に任せる。"""
        module = self.obj
        plan = self._mut_plan
        if not isinstance(plan, Indexed):
            return super().collect()
        hooks = known_collection_hooks(self)
        if hooks is None:
            self.config.stash.setdefault(_GENERIC_FILES_KEY, []).append(self.path)
            return super().collect()
        self._mut_recorded = RecordedModuleCollection.from_records(plan.records, hooks)
        try:
            return collect_recorded(self, module, self._mut_recorded)
        finally:
            self._mut_recorded = None

    def _genfunctions(self, name: str, funcobj: object) -> Iterator[pytest.Function]:
        """関数 1 つの item の生成(``pytest_pycollect_makeitem`` の既定の実装が呼ぶ)。記録から item を作っている
        間は、fixture の解決を同じ形の兄弟と共有する。"""
        recorded = self._mut_recorded
        if recorded is None:
            return super()._genfunctions(name, funcobj)
        return generate_functions(self, name, funcobj, recorded)

    def after_import(self, module: types.ModuleType) -> None:
        """収集の中で import した直後に、記録が実物を全部説明するなら保存し、説明しないなら理由を報告に足す。"""
        reason = self._mut_import_reason
        if reason is None:
            return
        self._mut_import_reason = None
        verified = verify_records(module, self._pytest_collects)
        if verified.problems:
            imported = self.config.stash[_IMPORTED_FILES_KEY]
            imported[-1] = (self.path, f"{reason} — 保存しない: " + "・".join(verified.problems))
            return
        write_cached(self.path.resolve(), module, verified.fixtures, items_cache_dir(self.config),
                     verified.records, Path(self.config.rootpath).resolve(), verified.dynamic)

    def _pytest_collects(self, name: str) -> bool:
        """pytest がこの名を test として集めるか(``python_functions`` / ``python_classes``)。"""
        return self.funcnamefilter(name) or self.classnamefilter(name)

    def realize(self, item: pytest.Function) -> None:
        """item の setup の前に、この file の module を 1 度だけ import し、item を実物の関数に替える。

        実物が記録と食い違えば ``RecordMismatch``(呼ぶ側が item を赤にし、記録を消す)。
        """
        real = self._mut_real_module
        if real is None:
            real = self.config.hook.pytest_doeff_import_hy_module(collector=self)
            recorded_names = [name for name, value in vars(self.obj).items() if callable(value)]
            check_no_unrecorded_items(recorded_names, real, self._pytest_collects, self.nodeid)
            swap_in_real_module_marks(self, self.obj, real)
            self._mut_real_module = real
        if item.obj is not getattr(real, item.originalname, None):
            swap_in_real_function(item, real)


def items_cache_dir(config: pytest.Config) -> Path:
    """item の記録のキャッシュの置き場(ini の doeff_adr_items_cache — 相対なら rootdir から・空なら利用者のキャッシュの dir)。"""
    configured = str(config.getini("doeff_adr_items_cache")).strip()
    if not configured:
        return DEFAULT_CACHE_DIR
    path = Path(configured).expanduser()
    return path if path.is_absolute() else Path(config.rootpath) / path


def _should_collect_hy_file(path: Path, config: pytest.Config) -> bool:
    root = Path(config.rootpath)
    patterns = _file_patterns(config)
    return _matches_file_patterns(path, root, patterns)


def _file_patterns(config: pytest.Config) -> list[str]:
    return [*DEFAULT_FILE_PATTERNS, *config.getini("doeff_adr_hy_files")]


@functools.cache
def _pattern_regex(patterns: tuple[str, ...]) -> re.Pattern[str]:
    """pattern の並びを 1 つの正規表現にする(並びごとに 1 度だけ)。

    収集と wiring の走査は file ごとに照合するので、pattern ごと・候補の path ごとに fnmatch を呼ぶと 1 回の収集で
    6 万回近くになっていた(agora-redesign #1334)。意味は fnmatch.fnmatch と同じ(normcase をかけ、全体に当てる)。
    """
    return re.compile("|".join(f"(?:{fnmatch.translate(os.path.normcase(p))})" for p in patterns))


def _matches_file_patterns(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """file の名・rootdir からの path・絶対 path のどれかが、executable ADR の pattern に当たるか。"""
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
    try:
        executable_adrs = _discover_executable_adrs(
            Path(config.rootpath),
            _file_patterns(config),
            _norecurse_dir_patterns(config),
            max_dirs=max_dirs,
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


def _discover_executable_adrs(
    root: Path,
    patterns: Sequence[str],
    norecurse: Sequence[str] = (),
    max_dirs: int = DEFAULT_WIRING_MAX_DIRS,
) -> set[Path]:
    executable_adrs: set[Path] = set()
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
            # .hy でない file(走査の大半)は Path を作る前に外す(agora-redesign #1551)。
            if not file_name.endswith(".hy"):
                continue
            path = Path(directory, file_name)
            if path.suffix == ".hy" and _matches_file_patterns(path, root, patterns):
                executable_adrs.add(path.resolve())
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
    """rootdir からの path(照合と報告のため)。

    収集と wiring の走査は rootdir の下の path を 1 file ずつ渡すので、文字の上で rootdir の下にあればそのまま
    相対にする。symlink の解決(realpath)は外れた時だけ — 毎回解決すると、収集の 1 回で 1 万回近く呼ばれて
    収集の時間の 1 割を占めていた(agora-redesign #1227 の実測)。

    文字の上で下にあるかは、まず文字列の頭で見る — ``is_relative_to`` / ``relative_to`` は path を部分に分けて
    作り直すので、収集と wiring の走査の 2,500 回ほどで目立っていた(agora-redesign #1551)。文字列の頭が合わない
    時(大小文字だけ違う等)は、今までの部分の比べと実の path の解決へ回す。
    """
    text = path.as_posix()
    root_text = root.as_posix()
    prefix = root_text if root_text.endswith("/") else root_text + "/"
    if text.startswith(prefix):
        return text[len(prefix):]
    if path.is_relative_to(root):
        return path.relative_to(root).as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _import_hy_file(path: Path, root: Path) -> types.ModuleType:
    root = _import_base_for_path(path.resolve(), root.resolve())
    path = path.resolve()
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
    """拡張子を除いた path の rootdir からの部分(module の名の素)。rootdir の下でなければ None。

    収集は file ごとに import の基の dir と module の名を決めるので、この部分を 2 度ずつ求める。文字の上で rootdir の
    下にある時は文字列を切って分ける — ``with_suffix`` / ``relative_to`` は path を作り直すので、420 file の収集で
    目立っていた(agora-redesign #1551)。文字列の頭が合わない時は今までの部分の比べに回す。
    """
    text = path.as_posix()
    root_text = root.as_posix()
    prefix = root_text if root_text.endswith("/") else root_text + "/"
    if text.startswith(prefix):
        relative = text[len(prefix):]
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
