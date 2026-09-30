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
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from hy.importer import HyLoader

from doeff_adr.item_cache import DEFAULT_CACHE_DIR, ProviderChecks, forget_cached, write_cached
from doeff_adr.lazy_collection import (
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
from doeff_adr.recorded_items import (
    GenerationTally,
    RecordedCollection,
    collect_recorded,
    generate_functions,
    generate_tests_impls,
    supported_pytest,
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
# 収集した Hy の file(item の有無によらず — 上の集合の元の 1 つ)。
_COLLECTED_HY_FILES_KEY = pytest.StashKey[list[Path]]()
# One measurement per session: the collection-finish report and an in-session
# gate test read the same verdict instead of walking rootdir twice.
_WIRING_VERDICT_KEY = pytest.StashKey[WiringVerdict]()
# 収集の終わりの報告(agora-redesign #1223): 記録から収集した file と、import して収集した file とその理由。
_INDEXED_FILES_KEY = pytest.StashKey[list[Path]]()
_IMPORTED_FILES_KEY = pytest.StashKey[list[tuple[Path, str]]]()
_DEPENDENCY_CHECKS_KEY = pytest.StashKey[DependencyChecks]()
_PROVIDER_CHECKS_KEY = pytest.StashKey[ProviderChecks]()
# session につき 1 度だけ用意する path と照合(rootdir の解決・キャッシュの置き場・executable ADR の pattern — agora-redesign #1551)。
_SESSION_PATHS_KEY = pytest.StashKey["SessionPaths"]()
_GENERATION_TALLY_KEY = pytest.StashKey[GenerationTally]()


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


# ---------------------------------------------------------------------------
# session につき 1 度の path と照合
# ---------------------------------------------------------------------------


def _posix(text: str) -> str:
    """OS の区切りの path の文字列を posix の形へ(posix ではそのまま)。"""
    return text if os.sep == "/" else text.replace(os.sep, "/")


@dataclass(frozen=True)
class HyFileMatcher:
    """executable ADR の pattern と rootdir を 1 度だけ用意した照合。

    候補は fnmatch と同じ意味で、file の名・rootdir からの path・絶対 path の 3 つ(``_matches_file_patterns`` と同じ)。rootdir の下
    の path は文字列の頭で分かるので、Path の分解と再生成(``relative_to`` — 収集で 1 file ごとに 3 度、走査でも 1 度)を
    繰り返さない(agora-redesign #1551)。文字の上で rootdir の下に無い path は、今までどおり解決(realpath)して相対にする。
    """

    root: Path
    root_prefix: str
    regex: re.Pattern[str]

    def relative_posix(self, text: str) -> str:
        """rootdir からの path(照合と報告のため)。文字の上で rootdir の下なら切り出し、そうでなければ解決して相対にする。"""
        if text.startswith(self.root_prefix):
            return _posix(text[len(self.root_prefix):])
        return _relative_posix(Path(text), self.root)

    def matches_text(self, text: str, name: str) -> bool:
        """``text``(絶対 path の文字列)の file が executable ADR の pattern に当たるか。"""
        candidates = (name, self.relative_posix(text), _posix(text))
        return any(self.regex.match(os.path.normcase(candidate)) for candidate in candidates)

    def matches(self, path: Path) -> bool:
        return self.matches_text(str(path), path.name)


@functools.cache
def _matcher(root_text: str, patterns: tuple[str, ...]) -> HyFileMatcher:
    """rootdir と pattern の組ごとに 1 度だけ照合を用意する。"""
    root = Path(root_text)
    return HyFileMatcher(root, root_text.rstrip(os.sep) + os.sep, _pattern_regex(patterns))


@dataclass(frozen=True)
class SessionPaths:
    """収集の 1 file ごとに要る path のうち、session で変わらない物(rootdir の解決は 1 度だけ・キャッシュの置き場・照合)。"""

    root: Path
    root_resolved: Path
    cache_dir: Path
    matcher: HyFileMatcher


def _session_paths(config: pytest.Config) -> SessionPaths:
    """session の path と照合(初めて要った時に用意し、stash に置く)。"""
    paths = config.stash.get(_SESSION_PATHS_KEY, None)
    if paths is None:
        root = Path(config.rootpath)
        paths = SessionPaths(root, root.resolve(), items_cache_dir(config), _matcher(str(root), tuple(_file_patterns(config))))
        config.stash[_SESSION_PATHS_KEY] = paths
    return paths


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    if file_path.suffix != ".hy":
        return None
    if not _session_paths(parent.config).matcher.matches(file_path):
        return None
    return DoeffAdrHyFile.from_parent(parent, path=file_path)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    """記録から収集した item は、fixture と skip の評価より先に実物の module と関数に替える(遅延 import)。"""
    parent = item.parent
    if isinstance(item, pytest.Function) and isinstance(parent, DoeffAdrHyFile):
        try:
            parent.realize(item)
        except RecordMismatch as exc:
            forget_cached(parent.resolved_path(), items_cache_dir(parent.config))
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
    tally = config.stash.get(_GENERATION_TALLY_KEY, None)
    if tally is not None:
        lines.append(tally.report())
    if indexed and not supported_pytest():
        lines.append(
            f"doeff-adr: pytest {pytest.__version__} は記録からの item の生成の対象外(9.1 系だけ)— 仮の module を pytest の Module の収集に渡した"
        )
    checks = config.stash.get(_DEPENDENCY_CHECKS_KEY, None)
    if checks is not None:
        lines.append(checks.report())
    return lines


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """collection の届いた file の集合(wiring の照合のため)。解決は item ごとでなく file ごとに 1 度(agora-redesign #1551)。

    収集した Hy の file は item が 0 本でも届いている(module ごと skip する file・検を持たない file — 以前は item から
    file を引いていたので、item の無い file は届いていないと報告されていた)。
    """
    paths: dict[Path, None] = dict.fromkeys(config.stash.get(_COLLECTED_HY_FILES_KEY, []))
    for item in items:
        paths.setdefault(item.path, None)
    config.stash[_COLLECTED_FILES_KEY] = frozenset(path.resolve() for path in paths)


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

    記録で説明できる file の item は、記録の名の並びから作る(``recorded_items`` — 属性の走査を省き、fixture の閉包を兄弟の
    関数で共有し、pytest_generate_tests が何もしないと分かる関数は Function を 1 つ直に作る・agora-redesign #1551)。hook は
    今までどおり呼ぶ。
    """

    _mut_real_module: types.ModuleType | None = None
    # 収集の中で import する理由(キャッシュに無い file)— import の直後の保存がこれを見る。setup の import では None。
    _mut_import_reason: str | None = None
    # 記録から集める file の、item の生成の状態(import で集める file は None)。
    _mut_recorded: RecordedCollection | None = None
    _mut_resolved: Path | None = None

    def resolved_path(self) -> Path:
        """この file の解決した path(symlink を辿った実体 — 1 度だけ解決する)。"""
        resolved = self._mut_resolved
        if resolved is None:
            resolved = self.path.resolve()
            self._mut_resolved = resolved
        return resolved

    def _getobj(self) -> types.ModuleType:
        paths = _session_paths(self.config)
        source = self.resolved_path()
        base = _import_base_for_path(source, paths.root_resolved)
        module_name = _module_name_for_path(source, base)
        checks = self.config.stash.setdefault(_DEPENDENCY_CHECKS_KEY, DependencyChecks())
        providers = self.config.stash.setdefault(_PROVIDER_CHECKS_KEY, ProviderChecks())
        match plan_collection(source, paths.cache_dir, paths.root_resolved, checks, providers):
            case Indexed(recorded):
                self.config.stash.setdefault(_INDEXED_FILES_KEY, []).append(self.path)
                self._mut_recorded = RecordedCollection(recorded)
                return stub_module(recorded, source, module_name)
            case NeedsImport(reason):
                # import が途中で終わる file(module ごと skip する等)も報告に載せるため、import の前に積む。
                self.config.stash.setdefault(_IMPORTED_FILES_KEY, []).append((self.path, reason))
                self._mut_import_reason = reason
                module = self.config.hook.pytest_doeff_import_hy_module(collector=self)
                self._mut_real_module = module
                return module

    def collect(self) -> Sequence[pytest.Item | pytest.Collector]:
        """記録で説明できる file は記録の名の並びから item を作り、それ以外(と対象外の pytest の版)は pytest の Module の収集に渡す。"""
        self.config.stash.setdefault(_COLLECTED_HY_FILES_KEY, []).append(self.path)
        self.obj  # noqa: B018 — 仮の module か実物の module を先に用意する(_getobj が記録の有無を決める)
        recorded = self._mut_recorded
        if recorded is None or not supported_pytest():
            return list(super().collect())
        return collect_recorded(self, recorded.recorded)

    def _genfunctions(self, name: str, funcobj: Callable[..., object]) -> Iterator[pytest.Function]:
        """pytest の item の生成の口(pytest の pytest_pycollect_makeitem が呼ぶ)。記録の関数はこの file の状態で作り、
        それ以外は pytest に任せる。"""
        recorded = self._mut_recorded
        if recorded is None or name not in recorded.recorded.functions or not supported_pytest():
            return super()._genfunctions(name, funcobj)
        tally = self.config.stash.setdefault(_GENERATION_TALLY_KEY, GenerationTally())
        return generate_functions(self, recorded, name, funcobj, generate_tests_impls(self.config), tally)

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
        paths = _session_paths(self.config)
        write_cached(self.resolved_path(), module, verified.fixtures, paths.cache_dir,
                     verified.records, paths.root_resolved, verified.dynamic)

    def _pytest_collects(self, name: str) -> bool:
        """pytest がこの名を test として集めるか(``python_functions`` / ``python_classes``)。"""
        return self.funcnamefilter(name) or self.classnamefilter(name)

    def realize(self, item: pytest.Function) -> None:
        """item の setup の前に、この file の module を 1 度だけ import し、仮の関数を持つ item を実物の関数に替える。

        替えるのは仮の関数(記録から作った物)を持つ item だけ — plugin が自分の関数で足した item はそのまま。
        実物が記録と食い違えば ``RecordMismatch``(呼ぶ側が item を赤にし、記録を消す)。
        """
        real = self._mut_real_module
        if real is None:
            real = self.config.hook.pytest_doeff_import_hy_module(collector=self)
            recorded_names = [name for name, value in vars(self.obj).items() if callable(value)]
            check_no_unrecorded_items(recorded_names, real, self._pytest_collects, self.nodeid)
            swap_in_real_module_marks(self, self.obj, real)
            self._mut_real_module = real
        if self._mut_recorded is not None and item.obj is vars(self.obj).get(item.originalname):
            swap_in_real_function(item, real)


def items_cache_dir(config: pytest.Config) -> Path:
    """item の記録のキャッシュの置き場(ini の doeff_adr_items_cache — 相対なら rootdir から・空なら利用者のキャッシュの dir)。"""
    configured = str(config.getini("doeff_adr_items_cache")).strip()
    if not configured:
        return DEFAULT_CACHE_DIR
    path = Path(configured).expanduser()
    return path if path.is_absolute() else Path(config.rootpath) / path


def _should_collect_hy_file(path: Path, config: pytest.Config) -> bool:
    return _session_paths(config).matcher.matches(path)


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
    return _matcher(str(root), tuple(patterns)).matches(path)


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
    return frozenset(Path(item.path).resolve() for item in session.items)


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
    """os.walk と同じ区別: symlink を辿って dir なら dir(辿れない・読めない物は file の側)。"""
    try:
        return entry.is_dir()
    except OSError:
        return False


def _discover_executable_adrs(
    root: Path,
    patterns: Sequence[str],
    norecurse: Sequence[str] = (),
    max_dirs: int = DEFAULT_WIRING_MAX_DIRS,
) -> set[Path]:
    """rootdir の下の executable ADR の file(解決した path)を全部集める。

    os.walk と同じ辿り方(symlink の dir は降りない・読めない dir は飛ばす・dir の数の上限)を scandir で行い、``.hy`` で終わらない
    名は Path を作る前に除く(agora-redesign #1551 — 1 回の走査で数万の file の Path を作っていた)。rootdir の下の file の
    解決した path は、rootdir を 1 度だけ解決した物から組み立てる(降りた dir に symlink は無い)。file 自身が symlink なら
    今までどおり解決する。
    """
    matcher = _matcher(str(root), tuple(patterns))
    prune = _pattern_regex(tuple(norecurse)) if norecurse else None
    root_resolved = root.resolve()
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
        for entry in entries:
            name = entry.name
            if _is_directory(entry):
                if name in IGNORED_DISCOVERY_DIRECTORIES or (prune is not None and prune.match(os.path.normcase(name))):
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
                executable_adrs.add(root_resolved / text[len(matcher.root_prefix):])
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
    """
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
    try:
        parts = path.with_suffix("").relative_to(root).parts
    except ValueError:
        return root
    if all(part.isidentifier() for part in parts):
        return root
    base = path.parent
    while (base / "__init__.py").exists() or (base / "__init__.hy").exists():
        base = base.parent
    return base


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
