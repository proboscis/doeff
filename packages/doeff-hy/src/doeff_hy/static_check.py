"""doeff-hy-check — Hy の source を本物の macro で展開し、pyright で型を検め、赤を .hy の行で返す。

    doeff-hy-check [PATH ...] [--root DIR] [--import-root DIR ...] [--pyright CMD] [--python EXE] [--json]
                   [--strict] [--baseline JSON | --write-baseline JSON] [--cache-dir DIR | --no-cache]
                   [--carry JSON [--carry-refresh-runs N]]

- PATH: 検める .hy の file か dir(既定 = --root)。dir は下の .hy を全部。
- --root: repo の根(既定 = 今の dir)。import の根で、macro の `require` もここから解く。
- --import-root: 根の下の別の import の根(例 `clients/hy`)。根の pyright の設定の
  `extraPaths` も import の根として読む。
- --strict: pyright の typeCheckingMode を strict にする。
- --write-baseline / --baseline: 今の赤を基点として書く / 基点に無い赤だけを止める(doeff_hy/static_baseline.py)。
  --json の診断には、基点に在る赤かを欄 `known` で付ける。
- --cache-dir / --no-cache: 依存の .hy の展開を、source と展開が通った file の記録ごとに保存して引く(doeff_hy/static_cache.py・
  既定の置き場 = $XDG_CACHE_HOME/doeff-hy-check か ~/.cache/doeff-hy-check)。
- --carry: 運びの file(doeff_hy/static_carry.py)。前の回から入力(その file の展開・使う名の宣言の表面・import の解き先・環境)の
  変わらない .hy は pyright に当てず、file の前の回の診断を運び、この回の運びで file を置き換える(無い file = 全部を当てる初回)。
  --carry-refresh-runs: 入力の変わらない file も、この回数のうちに 1 度は当て直す(見直し — 既定 12)。agora-redesign card ki-cbd1c93cfe7c。
- 新しい赤(基点が無ければ全部の赤)があれば exit 1、無ければ 0、道具として走れなければ 2。

仕組み:
0. 展開と pyright の間は、同じ機体で走る検と共有する機体の検の枠を 1 つ持つ(埋まっていれば待ち、待ちの上限を越えたら走らずに
   exit 2 — doeff_hy/check_slot.py。枠の正本の無い機体では取らない)。
1. 各 .hy を Hy の compiler で展開する(doeff-hy の macro は「型検査のための展開」=
   doeff_hy/static_view.py の切替の下で走る)。source の import は実行しない
   (macro の `require` だけは Hy の compile の常として macro の module を import する)。
2. 検める file が import する、根の下の別の .hy も同じく展開する(型を解くため・赤は出さない)。探すのは import の根と、
   import する file の dir から根までの親(pyright の探し方と同じ — search_dirs)。
3. 根の中身を symlink で映した一時の木に、展開した Python を `<名>.py` として置き、
   根の pyright の設定を写して pyright を撃つ。消費 repo の file は変えない。
4. pyright の診断を、展開した Python の位置 → Hy の式の位置(macro は合成した式にも
   包んでいる利用者の式の位置を付ける — doeff_hy/positions.hy)へ戻す。

設定: 根の `pyrightconfig.json`(無ければ `pyproject.toml` の `[tool.pyright]`)を土台にし、
`reportDeprecated` だけを error に上げる(文の位置に置いた Program / effect を
doeff_hy/macros.pyi の deprecated の overload で捕まえるため)。

捕まえるもの・捕まえないもの(2026-09-23 に agora-controllers の検体で測った一覧は
packages/doeff-hy/docs/static-check.md)。
"""

import argparse
import ast
import dataclasses
import functools
import json
import multiprocessing
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import hy
from doeff_hy_bytecode_guard import (
    SharedScope,
    gensym_renaming,
    macro_recording,
    record_is_current_here,
    value_reference,
)
from hy.compiler import hy_compile
from hy.errors import HyLanguageError
from hy.models import Object

from doeff_hy.binding_forms import Finding, ModuleScope
from doeff_hy.check_slot import SlotExpired, slot_label, take_slot
from doeff_hy.env_places import (
    PYRIGHT_THREADS_ENV,
    conversation_setting,
    pyright_threads_setting,
)
from doeff_hy.expansion_names import PREFIX as _EXPANSION_PREFIX
from doeff_hy.expansion_names import is_alias_import
from doeff_hy.static_baseline import (
    BaselineUnreadable,
    Split,
    baseline_json,
    errors_of,
    read_baseline,
    split,
)
from doeff_hy.static_cache import (
    BundleRow,
    CachedFinding,
    CachedProjection,
    CachedSpan,
    FileStat,
    CacheMiss,
    bundle_key,
    bundle_signature,
    default_cache_dir,
    load,
    load_bundle,
    place,
    row_is_current,
    stat_of,
    store,
    store_bundle,
)
from doeff_hy.static_carry import (
    DEFAULT_REFRESH_RUNS,
    CarriedRow,
    CarryRecord,
    CarryUnreadableError,
    FileInputs,
    FileRows,
    InputGraph,
    carry_text,
    next_record,
    own_digest,
    places_of,
    plan_carry,
    probe_environment,
    read_carry,
    semantics_digest,
    write_carry,
)
from doeff_hy.static_view import (
    DEFEFFECT_OUTCOMES_MODULE,
    STATIC_HELPER_IMPORTS,
    collect_findings,
    static_view,
)

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard import MacroRecord

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules", ".exp"})


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    column: int
    severity: str
    rule: str
    message: str

    def render(self) -> str:
        rule = f" ({self.rule})" if self.rule else ""
        return f"{self.path}:{self.line}:{self.column} - {self.severity}: {self.message}{rule}"


#: 展開した Python の範囲(0 始まりの行・列、終わりは含まない)と、対応する Hy の位置(1 始まり)。保存の形と同じ 1 つの型 —
#: 引いた保存から Projection へ戻す時に、十数万の位置を作り直さない。
Span = CachedSpan


@dataclass(frozen=True)
class Projection:
    source: Path
    module: str
    text: str
    spans: Sequence[Span]
    # macro が展開の時に出した所見(val / var の検査 — ADR-DOE-HY-006)を Hy の位置の診断にした物。
    findings: tuple[Diagnostic, ...] = ()
    # 展開が通った file の記録(Hy の版・macro と補助と型検査の後処理の file と sha256 — 保存を照らす・agora-redesign #3862)。
    used: "MacroRecord" = dataclasses.field(kw_only=True)


@dataclass(frozen=True)
class CompileFailure:
    source: Path
    diagnostic: Diagnostic


@dataclass(frozen=True)
class Closure:
    """検める file と、それが import する根の下の .hy の展開。"""

    projections: dict[Path, Projection]
    failures: list[CompileFailure]


@dataclass(frozen=True)
class HyPosition:
    line: int
    column: int


@dataclass(frozen=True)
class Checked:
    """診断と、赤にしない注記。"""

    diagnostics: list[Diagnostic]
    notes: list[str]


def _absolute(path: Path) -> Path:
    """絶対 path に正規化する。symlink は辿らない(根を symlink で映した木でも根の中に留まる)。"""
    return Path(os.path.normpath(path.absolute()))


def in_root(root: Path, path: Path) -> Path | None:
    """名指した path を根の綴り(_absolute — symlink を辿らない絶対 path)の下へ写す。綴りが根の下ならそのまま、symlink を辿ると
    根の下なら根の綴りへ写し直し(相対 path を繋ぐ Path.cwd() は辿った先の綴りを返す — card ki-ed254bf53094)、どちらでもなければ
    None(根の外)。"""
    absolute = _absolute(path)
    if absolute.is_relative_to(root):
        return absolute
    resolved, base = path.resolve(), root.resolve()
    return root / resolved.relative_to(base) if resolved.is_relative_to(base) else None


def hy_files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".hy":
            found.append(_absolute(path))
        elif path.is_dir():
            for candidate in sorted(path.rglob("*.hy")):
                if not _SKIP_DIRS.intersection(candidate.relative_to(path).parts):
                    found.append(_absolute(candidate))
    return list(dict.fromkeys(found))


def module_name(roots: list[Path], source: Path) -> str:
    """一番深い import の根からの相対 path を module 名にする。"""
    base = max((r for r in roots if source.is_relative_to(r)), key=lambda r: len(r.parts))
    parts = list(source.relative_to(base).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _child_pairs(generated: ast.AST, original: ast.AST) -> Iterator[tuple[ast.AST, ast.AST]]:
    """展開した木と、それを文字列にして読み直した木を同じ順に歩く。形が食い違えばそこで降りない。"""
    yield generated, original
    left = list(ast.iter_child_nodes(generated))
    right = list(ast.iter_child_nodes(original))
    if len(left) != len(right):
        return
    for a, b in zip(left, right, strict=True):
        if type(a) is type(b):
            yield from _child_pairs(a, b)


def _position(node: ast.AST, name: str) -> int | None:
    value = vars(node).get(name)
    return value if isinstance(value, int) else None


def _span(generated: ast.AST, original: ast.AST) -> Span | None:
    """読み直した木の node の範囲と、元の(Hy が位置を付けた)node の Hy の位置を組にする。"""
    g_line, g_col = _position(generated, "lineno"), _position(generated, "col_offset")
    g_end_line = _position(generated, "end_lineno")
    g_end_col = _position(generated, "end_col_offset")
    h_line, h_col = _position(original, "lineno"), _position(original, "col_offset")
    if g_line is None or g_col is None or g_end_line is None or g_end_col is None:
        return None
    if h_line is None or h_col is None:
        return None
    # Hy は col_offset に 1 始まりの列を入れる(hy.compiler の位置の写し)。
    return Span((g_line - 1, g_col), (g_end_line - 1, g_end_col), h_line, max(h_col, 1))


#: 定義の記帳(実行時の内観のための属性 — 型の意味を持たない)だけが使う module。展開の後に使い手が残らなければ
#: import を外す(pyright strict で doeff_hy.quoted_forms の stub 無し・doeff_hy.declarations の重複の import に
#: なっていた — agora の画面の core の 3 file で 241 件・agora-redesign #2153)。doeff_hy.pytest_items は pytest の
#: item の記録(deftest・defadr・defsemgrep・pytestmark が足す — 収集のための記帳)だけが使う(stub 無しの赤 — #2214)。
BOOKKEEPING_MODULES: frozenset[str] = frozenset(
    {"doeff_hy.declarations", "doeff_hy.quoted_forms", "doeff_hy.record", "doeff_hy.pytest_items"}
)


def _bookkeeping_statement(statement: ast.stmt) -> bool:
    """型検査に見せない module の直下の文か: 定義の記帳 `setattr(名, '__doeff_…__', …)`、pytest の item の記録
    `doeff_hy.pytest_items.record_at_import(globals(), …)`(doeff_hy/pytest_items.py の `_record_form` が足す — 収集の
    ための記帳・agora-redesign #2214)と、Hy が `(require …)` を compile した残り `hy.macros.require(…)`
    (macro の取り込みは展開の時に済んでいる)。"""
    match statement:
        case ast.Expr(
            value=ast.Call(func=ast.Name(id="setattr"), args=[_, ast.Constant(value=str(attribute)), *_])
        ):
            return attribute.startswith("__doeff_")
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Attribute(value=ast.Name(id="doeff_hy"), attr="pytest_items"),
                    attr="record_at_import",
                )
            )
        ):
            return True
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Attribute(value=ast.Name(id="hy"), attr="macros"), attr="require"
                )
            )
        ):
            return True
        case _:
            return False


def _effects_reads(statement: ast.stmt) -> ast.stmt | None:
    """定義の記帳 `setattr(名, '__doeff_effects__', doeff_hy.declarations.effect_types(場所, tuple([A, B])))` を、
    doeff_hy.declarations を呼ばない形 `setattr(名, '__doeff_effects__', (A, B))` に縮めた文にするため(それ以外は None)。
    宣言 :effects にだけ出る効果の名(handler の節が下請けの関数を通して出す効果)を、型検査の中で読まれた名に残す —
    記帳ごと外すと、その名の import が strict の reportUnusedImport の赤になり、書き手は直せない(import を消すと実行の時に
    :effects の宣言が名を解けない — agora-redesign #3871 の後)。空の宣言 `:effects []`(effect を出さない)も `()` で残す:
    道具 static_stub が .pyi の答えの Program の effect の位置を Never にする(読まれる名は無い・agora-redesign #2939)。"""
    match statement:
        case ast.Expr(
            value=ast.Call(
                func=ast.Name(id="setattr"),
                args=[
                    target,
                    ast.Constant(value="__doeff_effects__"),
                    ast.Call(args=[_, ast.Call(func=ast.Name(id="tuple"), args=[ast.List(elts=names)])]),
                ],
            )
        ):
            reads = ast.Expr(
                value=ast.Call(
                    func=ast.Name(id="setattr", ctx=ast.Load()),
                    args=[target, ast.Constant(value="__doeff_effects__"), ast.Tuple(elts=names, ctx=ast.Load())],
                    keywords=[],
                )
            )
            return ast.copy_location(reads, statement)
        case _:
            return None


def _referenced_roots(statements: list[ast.stmt]) -> frozenset[str]:
    """文の中で読まれている名(属性の連なりは根の名)— 外してよい import を決めるため。"""
    return frozenset(
        node.id
        for statement in statements
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    )


def _bound_name(alias: ast.alias) -> str:
    """import が束縛する名(`import a.b` は `a`・`as` があればその名)。"""
    return alias.asname or alias.name.split(".")[0]


def _dotted(node: ast.expr) -> str | None:
    """名か名から始まる属性の連なりの綴り(`a.b.c`)— それ以外の式は None。"""
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(value=value, attr=attr):
            base = _dotted(value)
            return None if base is None else f"{base}.{attr}"
        case _:
            return None


def _referenced_paths(statements: list[ast.stmt]) -> frozenset[str]:
    """文の中で読まれている名と属性の連なり(`a.b.c` を読めば `a`・`a.b`・`a.b.c` の全部)— `import a.b` が読まれているかを
    module の綴りで決めるため。`import doeff_hy.declarations` と `import doeff_hy.record` は同じ根の名 `doeff_hy` を束縛するので、
    根の名だけでは片方しか読まれていない時に両方を残してしまう(defrecord の :check の展開 — agora-redesign #2252)。"""
    return frozenset(
        path
        for statement in statements
        for node in ast.walk(statement)
        if isinstance(node, (ast.Name, ast.Attribute))
        if (path := _dotted(node)) is not None
    )


def _alias_read(alias: ast.alias, used: frozenset[str]) -> bool:
    """import の 1 つの名が読まれているか(`as` があればその名・無ければ書いた綴り — `import a.b` は連なり `a.b`・
    `from m import x` は `x`)。"""
    return (alias.asname or alias.name) in used


def _without_unused_bookkeeping(statement: ast.stmt, used: frozenset[str]) -> ast.stmt | None:
    """記帳だけが使う module(BOOKKEEPING_MODULES)の import から、もう読まれていない名を 1 つずつ外す。名が 1 つも残らなければ
    文ごと外す(None)。1 つの文が読まれる名と読まれない名を並べる形(`import doeff_hy.declarations, doeff_hy.record` — defrecord の
    展開)で、読まれない側だけが strict の reportUnusedImport になっていた(agora-redesign #2252)。"""
    match statement:
        case ast.Import(names=names):
            kept = [a for a in names if a.name not in BOOKKEEPING_MODULES or _alias_read(a, used)]
            narrowed: ast.stmt = ast.Import(names=kept)
        case ast.ImportFrom(module=str(module), level=0, names=names) if module in BOOKKEEPING_MODULES:
            kept = [a for a in names if _alias_read(a, used)]
            narrowed = ast.ImportFrom(module=module, names=kept, level=0)
        case _:
            return statement
    match kept:
        case []:
            return None
        case _ if len(kept) == len(names):
            return statement
        case _:
            return ast.copy_location(narrowed, statement)


def _doeff_hy_module(module: str) -> bool:
    """doeff-hy 自身の module か(macro が定義ごとに import を合成する先)。"""
    return module.split(".")[0] == "doeff_hy"


def _synthesized_import(statement: ast.stmt) -> bool:
    """macro が定義ごとに合成する import の文か(利用者の重複の import は赤のまま残すため、重ねを 1 つにするのはこれだけ):
    doeff-hy 自身の module の import と、module を展開の名の頭 `_doeff_`(expansion_names.PREFIX)の別名で束ねる
    `import <module> as _doeff_<名>`(持ち主の package の定義の macro が出す形 — doeff-cluster の defsystem が系ごとに出す系の型と
    構成子の module の import。doeff-hy は持ち主の module の綴りを持たない)と、defeffect が
    答えの分け方を置く時の `import doeff_core_effects.outcomes`(static_view.DEFEFFECT_OUTCOMES_MODULE)と、handle・defhandler が
    node を `_doeff_<名>` の別名で束ねる `from doeff import Pass as _doeff_Pass` と、型を建てる定義が型の本体で読む
    `from typing import ClassVar as _doeff_ClassVar` など(expansion_names.is_alias_import — 閉じた表の綴りだけ。関数・class の
    本体の中に残すと、class の本体では節の関数と入れ子の型の本体が名を module の段から引いて pyright が未定義と読んだ)。
    書き手が `_doeff_` の別名を書くのは for/do の `(import doeff [do :as _doeff-do])`(`from … import` の形)だけなので、
    `import <module> as _doeff_<名>` の形は書き手の import と取り違えない。"""
    match statement:
        case ast.Import(names=names):
            return all(
                _doeff_hy_module(alias.name)
                or (alias.name == DEFEFFECT_OUTCOMES_MODULE and alias.asname is None)
                or (alias.asname is not None and alias.asname.startswith(_EXPANSION_PREFIX))
                for alias in names
            )
        case ast.ImportFrom(module=str(module), level=0, names=names):
            return _doeff_hy_module(module) or all(
                is_alias_import(module, alias.name, alias.asname) for alias in names
            )
        case _:
            return False


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _nested_imports(node: ast.AST, inside: bool) -> Iterator[ast.stmt]:
    """node の中(入れ子の文を含む)の、関数・class の本体の中に在る macro 合成の import の文(`_synthesized_import`)を
    書いた順に返す。inside = node がすでに関数・class の本体の中か。"""
    here = inside or isinstance(node, _SCOPES)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt) and here and _synthesized_import(child):
            yield child
        else:
            yield from _nested_imports(child, here)


#: 文の本体(`body`)を持つ node — import を外して空になった本体に `pass` を置くため(空の本体は Python の文として書けない)。
_BODIED = (
    *_SCOPES,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.TryStar,
    ast.ExceptHandler,
    ast.match_case,
)


class _WithoutNestedImports(ast.NodeTransformer):
    """関数・class の本体の中の macro 合成の import の文(`_nested_imports` が返す物)を外すため。文の 1 つだけを持っていた
    本体は `pass` にする。"""

    def __init__(self) -> None:
        self._mut_depth = 0

    def _scope(self, node: ast.AST) -> ast.AST:
        """関数・class の本体の中を読む間だけ深さを 1 つ増やす(その中の import が外す対象 — module の段の物は外さない)。"""
        self._mut_depth += 1
        try:
            return self.generic_visit(node)
        finally:
            self._mut_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        """関数の本体の中を読む(`_scope`)。"""
        return self._scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        """async 関数の本体の中を読む(`_scope`)。"""
        return self._scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        """class の本体の中を読む(`_scope`)。"""
        return self._scope(node)

    def visit_Import(self, node: ast.Import) -> ast.Import | None:
        """関数・class の本体の中の macro 合成の `import` は外す(`_nested_imports` が module の段へ集めた物)。"""
        return None if self._mut_depth and _synthesized_import(node) else node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom | None:
        """関数・class の本体の中の macro 合成の `from … import` は外す(`visit_Import` と同じ)。"""
        return None if self._mut_depth and _synthesized_import(node) else node

    def generic_visit(self, node: ast.AST) -> ast.AST:
        """子を読み、import を外して本体が空になった node には `pass` を置く。"""
        visited = super().generic_visit(node)
        if isinstance(node, _BODIED) and not node.body:
            node.body = [ast.Pass()]
        return visited


def with_module_level_imports(tree: ast.Module) -> ast.Module:
    """関数・class の本体の中に macro が置いた import(`_synthesized_import` — doeff-hy の module・`_doeff_` の別名で束ねる module・defeffect の
    答えの分け方の module)を、囲む module の直下の文の前へ移す(macros.hy の `_scoped-definition` の型検査のための展開の側)。

    定義の macro は展開の import を書いた所に置く。関数の中の `import doeff_hy.record` は `doeff_hy` を外の関数全体の局所の名にし、
    外の関数が先に module の段の `doeff_hy` を読むと、pyright は未束縛と読む。class の本体の import は class の属性で、入れ子の型の
    本体と method は名を module の段から引く(Python と同じ)ので、定義が読む `doeff_hy.record` を解けない。実行時の展開は関数の中を
    包んで局所の名を閉じ込める(`_scoped-definition`)が、型検査のための展開は型の宣言(class・def)を外の関数に残す形なので包めない —
    import を module の段へ集め、以後の掃除(`without_bookkeeping` の重ねの畳みと、読まれない記帳の import の除去)に任せる。
    module の段の文・module の段の `if` の中の import は動かさない(もう module の段の名)。木はこの展開だけの物なので、その場で替える。"""
    moved = [tuple(_nested_imports(statement, inside=False)) for statement in tree.body]
    _WithoutNestedImports().visit(tree)
    tree.body = [
        placed
        for imports, statement in zip(moved, tree.body, strict=True)
        for placed in (*imports, statement)
    ]
    return tree


def with_canonical_gensyms(tree: ast.Module) -> ast.Module:
    """展開の木の Hy の gensym の名(識別子と文字列の定数)を、file の中の数えの順に 1 から振り直す(agora-redesign #3869)。

    gensym の数えは process に 1 つなので、同じ source でも、同じ process で先に何を展開したか・並べた時にどの process で
    展開したかで名が変わり、保存の中身が実行の仕方に依っていた。振り直しの規則は、テストの実行の側の code の正準化と同じ
    1 つ(doeff_hy_bytecode_guard.gensym_renaming)。替え方は 1 対 1 なので、名の一意性は保たれる。木はこの展開だけの物
    なので、その場で替える。"""
    nodes = tuple(ast.walk(tree))
    rename = gensym_renaming(
        text for node in nodes for _, value in ast.iter_fields(node) for text in _field_texts(value)
    )
    for node in nodes:
        for name, value in ast.iter_fields(node):
            match value:
                case str():
                    setattr(node, name, rename(value))
                case list() if all(isinstance(item, str) for item in value):
                    setattr(node, name, [rename(item) for item in value])
                case _:
                    pass
    return tree


def _field_texts(value: object) -> tuple[str, ...]:
    """AST の欄 1 つの文字列(識別子・文字列の定数・global / nonlocal の名の列)。"""
    match value:
        case str():
            return (value,)
        case list():
            return tuple(item for item in value if isinstance(item, str))
        case _:
            return ()


def without_bookkeeping(tree: ast.Module) -> ast.Module:
    """型検査のための展開から、型の意味を持たない記帳を外す(pyright strict の誤検出の元を展開の側で絶つ)。

    - 定義の記帳と `hy.macros.require` の残り(_bookkeeping_statement)を外す。ただし宣言 :effects の記帳は、名を読む形に
      縮めて残す(_effects_reads — 宣言にだけ出る効果の名の import を、読まれない import にしない)。
    - module の直下で macro が定義ごとに出す import の文(doeff-hy の module と `_doeff_` の別名で束ねる module —
      _synthesized_import)が繰り返されたら最初の 1 つだけを残す。
    - 記帳だけが使う module(BOOKKEEPING_MODULES)の import の名(1 つの文に並んだ名も 1 つずつ)と Hy の `import hy` で、
      もう読まれない物を外す。
    消費 repo の利用者が書いた式は外さない(外すのは macro が合成した文の形だけ)。"""
    kept = [
        reads if (reads := _effects_reads(statement)) is not None else statement
        for statement in tree.body
        if _effects_reads(statement) is not None or not _bookkeeping_statement(statement)
    ]
    imports = (ast.Import, ast.ImportFrom)
    seen_keys = [ast.dump(s) if _synthesized_import(s) else None for s in kept]
    first = [
        statement
        for index, statement in enumerate(kept)
        if seen_keys[index] is None or seen_keys[index] not in seen_keys[:index]
    ]
    used = _referenced_paths([s for s in first if not isinstance(s, imports)])
    tree.body = [
        narrowed
        for s in first
        if not _unused_hy_import(s, used)
        if (narrowed := _without_unused_bookkeeping(s, used)) is not None
    ]
    return tree


def _unused_hy_import(statement: ast.stmt, used: frozenset[str]) -> bool:
    """Hy の compiler が module ごとに置く `import hy` で、記帳を外した後に読まれなくなった物か
    (`hy.macros.require` の残りだけが読んでいた — strict の reportUnusedImport の元)。"""
    match statement:
        case ast.Import(names=[ast.alias(name="hy", asname=None)]):
            return "hy" not in used
        case _:
            return False


def with_static_helpers(tree: ast.Module) -> ast.Module:
    """静的な展開の macro が参照する補助の名の import を、module の頭(`from __future__` の後)に 1 度だけ置く。

    macro は静的な展開では defk / defhandler / `<-` ごとの import を出さない(static_view.STATIC_HELPER_IMPORTS の註 —
    1 つの名の宣言が 64 を超えると pyright が型の推論をやめる)。置く文は Hy の source に無いので位置を持たせない
    (_span が組にしない — 補助の import に赤は出ない)。module が読む補助の名だけを置く(strict の
    reportUnusedImport を出さないため)。`hy.models`(Hy の keyword の literal が compile される先)を読む module には
    `import hy.models` を足す(`import hy` だけでは pyright が属性 models を知らない)。"""
    used = _referenced_roots(tree.body)
    reads_models = any(
        isinstance(node, ast.Attribute)
        and node.attr == "models"
        and isinstance(node.value, ast.Name)
        and node.value.id == "hy"
        for node in ast.walk(tree)
    )
    declared: list[ast.stmt] = ast.parse(
        STATIC_HELPER_IMPORTS + ("import hy.models\n" if reads_models else "")
    ).body
    helpers: list[ast.stmt] = [
        statement
        for statement in (_only_used(s, used) for s in declared)
        if statement is not None
    ]
    for statement in helpers:
        for node in ast.walk(statement):
            for field in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
                if field in vars(node):
                    delattr(node, field)
    head: int = 0
    for statement in tree.body:
        if not (isinstance(statement, ast.ImportFrom) and statement.module == "__future__"):
            break
        head += 1
    tree.body[head:head] = helpers
    return tree


def _only_used(statement: ast.stmt, used: frozenset[str]) -> ast.stmt | None:
    """補助の import の文から、module が読む名だけを残す(1 つも読まなければ文ごと要らない = None)。
    `import pytest as _doeff_pytest`(deftest の decorator が引く)も読む時だけ置く。`import hy.models` は `hy` を
    束縛し、読む時だけ置く呼び手が足すので、この判じでも残る。"""
    match statement:
        case ast.ImportFrom(module=module, names=names, level=level):
            kept = [alias for alias in names if _bound_name(alias) in used]
            return ast.ImportFrom(module=module, names=kept, level=level) if kept else None
        case ast.Import(names=names):
            kept = [alias for alias in names if _bound_name(alias) in used]
            return ast.Import(names=kept) if kept else None
        case _:
            return statement


def project(root: Path, roots: list[Path], source: Path) -> Projection | CompileFailure:
    """1 つの .hy を型検査のための展開で Python にし、位置の対応表を作る。"""
    text = source.read_text(encoding="utf-8")
    name = module_name(roots, source)
    module = ModuleType(name)
    module.__file__ = str(source)
    relative = str(source.relative_to(root))
    # source は Hy の reader で 1 度だけ読む(agora-redesign #3869)。compile は前の form を処理してから次の form を読む(file の中で
    # 定義した reader macro を後ろの form に効かせるため)ので、先に全部を読まず、compile が form を 1 つ読むたびに同じ form を
    # 一番外の setv の所見(ModuleScope)に見せる(form を最後まで抱えない)。以前は所見のために新しい reader で読み直し、展開の
    # 約 2 割を払い、file の中の reader macro を知らずに落ちていた。
    forms = hy.read_many(text, filename=str(source))
    scope = ModuleScope()
    lazy = hy.models.Lazy(_shown_to(scope, forms))
    lazy.source, lazy.filename, lazy.reader = forms.source, forms.filename, forms.reader
    try:
        with static_view(), collect_findings() as found, macro_recording(module) as recording:
            compiled = hy_compile(lazy, module, filename=str(source), source=text)
        # hy_compile は get_expr=True の時だけ (Module, Expression) の組を返す。
        if not isinstance(compiled, ast.Module):
            raise TypeError(f"hy_compile が module を返さなかった: {type(compiled).__name__}")
        tree = with_canonical_gensyms(
            with_static_helpers(without_bookkeeping(with_module_level_imports(compiled)))
        )
    except HyLanguageError as error:
        line = error.lineno if isinstance(error.lineno, int) else 1
        column = error.offset if isinstance(error.offset, int) else 1
        message = str(error.msg) if error.msg else str(error)
        return CompileFailure(
            source, Diagnostic(relative, line, column, "error", "hy-compile", message)
        )
    rendered = ast.unparse(tree)
    reparsed = ast.parse(rendered)
    spans = [span for pair in _child_pairs(reparsed, tree) if (span := _span(*pair)) is not None]
    # module の直下の setv は macro の外なので、source の一番外の並びを別に見る(ADR-DOE-HY-006)— compile と並んで見た所見。
    top_level = scope.findings
    return Projection(
        source,
        name,
        rendered,
        tuple(spans),
        tuple(_finding_diagnostic(relative, f) for f in (*found, *top_level)),
        used=recording.record(
            text, also=(value_reference(_POST_PROCESSING_MODULE, "POST_PROCESSING"),)
        ),
    )


#: 展開の結果を変える macro の外の処理(関数・class の本体の import の module の段への移し・記帳の外し・補助の import の足し・gensym の名の振り直し・位置の対応・所見)の入口 —
#: 型検査の展開の保存(static_cache)の記録は、この値の閉包の digest で後処理の変更を見る(``project`` が呼ぶ関数を名で辿る)。
#: module の属性の ``project`` ではなく、定義した時の関数を持つ(検が ``project`` を差し替えても、記録は変わらない)。
POST_PROCESSING = (project,)

#: 記録が POST_PROCESSING を名指す module の名 — import で引ける名(``__spec__.name``)。``python -m doeff_hy.static_check`` で撃つと
#: この file は親で ``__main__``・展開の子で ``__mp_main__`` の名で読まれ、``__name__`` で名指すと、子の記録(``__mp_main__`` の
#: ``project`` を辿った digest)と次の実行の親の照らし(``__main__`` の ``project`` を辿った digest)が持ち主の名の違う関数を辿って
#: 合わず、保存した展開を 1 つも引けなかった(agora-redesign card ki-1475765a3f18)。引ける名なら、親も子もその名で import した
#: 同じ file を辿る。
_POST_PROCESSING_MODULE = __spec__.name if __spec__ is not None else __name__


def _shown_to(scope: ModuleScope, forms: Iterable[Object]) -> Iterator[Object]:
    """forms を 1 つずつ渡しながら、渡す前に同じ form を scope に見せる(compile と所見が 1 度の読みを分け合うため)。"""
    for form in forms:
        scope.visit(form)
        yield form


def _finding_diagnostic(relative: str, finding: Finding) -> Diagnostic:
    """macro の所見(binding_forms.Finding)を、この道具の診断の形にする(赤か警告か・規則の名・文言)。"""
    return Diagnostic(
        relative,
        max(finding.line, 1),
        max(finding.column, 1),
        finding.severity.value,
        finding.rule,
        finding.message,
    )


@dataclass(frozen=True)
class _Listing:
    """dir の直下の file の名と dir の名(dir が無い・読めない時は両方空 — Path.is_file と同じく、無い物は file でない)。"""

    files: frozenset[str]
    dirs: frozenset[str]


def _listing(directory: Path) -> _Listing:
    try:
        with os.scandir(directory) as entries:
            scanned = list(entries)
        return _Listing(
            frozenset(entry.name for entry in scanned if entry.is_file()),
            frozenset(entry.name for entry in scanned if entry.is_dir()),
        )
    except OSError:
        return _Listing(frozenset(), frozenset())


def _present_candidates(
    dirs: tuple[Path, ...], name: str, listing: Callable[[Path], _Listing]
) -> tuple[Path, ...]:
    """import の名 name の候補(<名>.hy と <名>/__init__.hy)のうち、実在する file の絶対 path。dir を 1 つずつ下りて、無い dir から
    先は引かない(候補ごとに stat しない・同じ dir の一覧は呼び手の覚えが 1 度だけ引く)。"""
    *packages, leaf = name.split(".")
    present: list[Path] = []
    for directory in dirs:
        current = directory
        for part in packages:
            if part not in listing(current).dirs:
                break
            current = current / part
        else:
            here = listing(current)
            if f"{leaf}.hy" in here.files:
                present.append(_absolute(current / f"{leaf}.hy"))
            if leaf in here.dirs and "__init__.hy" in listing(current / leaf).files:
                present.append(_absolute(current / leaf / "__init__.hy"))
    return tuple(present)


def search_dirs(root: Path, roots: list[Path], source: Path) -> list[Path]:
    """source が書く絶対の import の .hy を探す dir を、探す順に並べる(依存の展開の探し道の 1 か所)。

    import の根(import_roots — repo の根と extraPaths)の後に、source の dir から根まで親を辿った dir を置く。pyright は
    絶対の import を根・extraPaths・標準ライブラリ・環境で解けない時、import する file の dir から根へ向けて親の dir を順に
    探す(`hy scripts/x.hy` で撃つ script の隣の module `(import e2e_wire …)` がこれで解ける)。前は import の根だけを探したので、
    1 file だけを検めると隣の .hy が展開されず、pyright が import を解けなかった(reportMissingImports と、そこから来る
    Unknown の赤)。dir ごと検めると隣の .hy も検める file として展開されるので解けていた — 同じ file の答えが検め方で
    分かれた(agora-redesign #2898)。

    展開は在る候補を全部集める(先の dir で当たっても後の dir を探す)。pyright が読まない展開(標準ライブラリと同じ名の
    隣の .hy など — pyright は標準ライブラリを先に解く)は余分な展開になるだけで、検める file の赤を変えない
    (dir ごと検める時もその .hy は展開されて木に在る)。"""
    local = [d for d in (source.parent, *source.parent.parents) if d.is_relative_to(root)]
    return list(dict.fromkeys([*roots, *local]))


def import_roots(root: Path, settings: dict[str, object]) -> list[Path]:
    """import の根 = repo の根 + pyright の設定の extraPaths のうち根の下の物。"""
    roots = [root]
    extra = settings.get("extraPaths")
    for entry in extra if isinstance(extra, list) else []:
        candidate = _absolute(root / str(entry))
        if candidate.is_relative_to(root) and candidate not in roots:
            roots.append(candidate)
    return roots


def imported_modules(projection: Projection) -> set[str]:
    names: set[str] = set()
    package = projection.module.rsplit(".", 1)[0] if "." in projection.module else ""
    if projection.source.name == "__init__.hy":
        package = projection.module
    for node in ast.walk(ast.parse(projection.text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".") if package else []
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, *(node.module.split(".") if node.module else [])])
            else:
                base = node.module or ""
            if base:
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names)
    expanded: set[str] = set()
    for name in names:
        parts = name.split(".")
        expanded.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return expanded


def _from_cache(source: Path, module: str, cached: CachedProjection) -> Projection:
    """保存した展開を、この実行の Projection に戻す(展開し直さずに済ませるため)。"""
    return Projection(
        source,
        module,
        cached.text,
        cached.spans,
        tuple(
            Diagnostic(f.path, f.line, f.column, f.severity, f.rule, f.message)
            for f in cached.findings
        ),
        used=cached.used,
    )


def _to_cache(projection: Projection) -> CachedProjection:
    """展開の結果を保存の形にする(次の実行で依存を展開し直さないため)。"""
    return CachedProjection(
        projection.text,
        projection.spans,
        tuple(
            CachedFinding(f.path, f.line, f.column, f.severity, f.rule, f.message)
            for f in projection.findings
        ),
        projection.used,
        tuple(sorted(imported_modules(projection))),
    )


#: 保存から引けない依存の展開を並べる子の process の数 — 機体の検の枠の宣言が持つ(正本の公開の口 process_width が答え、
#: SlotHeld.width で届く)。この定数は宣言が届かない機体(正本の無い宿・古い正本・宣言を読めない時)の既定で、値の持ち主ではない
#: (agora-redesign #3869・card ki-2320b23ba160 の盲検 B の指す 2 つ目の持ち主 — 設計検証 lt-5KHSMYM9R1HW7AKJBRG32NNF4S)。
#: 実際の数は min(この数, この process が使える CPU の数, その段の引けない file の数)。
#: 4(2026-10-11): 36 core の機体で同じ作業木の検査 2 本と別の検 4 本が重なり load 163 になった(展開の子と forkserver で 30 本超)。
#: 環境変数で替える口は持たない — 口が在ると、値を替えた会話の検だけが別の数で数え、枠の数え方が割れる(正本の頭の註)。
EXPANDER_LIMIT = 4


def expander_limit(declared: int | None) -> int:
    """展開の子の数の上限を決める(declared = 枠の正本が答えた 1 枠の中の並走の数・届かなければ None で EXPANDER_LIMIT)。"""
    return EXPANDER_LIMIT if declared is None else declared


class ExpansionWorkerLostError(RuntimeError):
    """依存の展開を受け持った子の process が、答えを返さずに落ちた(答えの無い file を名指す — main は道具の失敗 exit 2 にする)。"""


@dataclass(frozen=True)
class _Hit:
    """保存から引けた展開と、それが import する module の名と、束に書く行。"""

    projection: Projection
    imports: frozenset[str]
    relative: str
    row: BundleRow


@dataclass(frozen=True)
class _Miss:
    """保存から引けなかった source と、展開した後に保存する鍵(保存しない実行では None)・束の行に要る物。"""

    source: Path
    key: str | None
    relative: str = ""
    module: str = ""
    stat: FileStat | None = None


def _looked_up(
    root: Path,
    roots: list[Path],
    source: Path,
    cache_dir: Path | None,
    bundle: dict[str, BundleRow] | None = None,
) -> _Hit | _Miss:
    """保存から引く(引けなければ、展開した後に保存する鍵を持った _Miss)。束に載る行は、source の stat が書いた時と同じなら
    source も entry も open せずに引く(card ki-2829daea29fa)。"""
    if cache_dir is None:
        return _Miss(source, None)
    module = module_name(roots, source)
    relative = str(source.relative_to(root))
    stat = stat_of(source)  # 読む前に取る(読む間に変わっても、次の実行は食い違いとして読み直す)
    row = (bundle or {}).get(relative)
    if row is not None and row_is_current(row, module, stat):
        return _hit(source, relative, module, row.cached, row.place, stat)
    key = place(source.read_text(encoding="utf-8"), module, relative)
    if row is not None and row.place == key and row.module == module and record_is_current_here(row.cached.used):
        return _hit(source, relative, module, row.cached, key, stat)
    match load(cache_dir, key):
        case CachedProjection() as cached:
            if cached.imports is None:
                # import の名を持たない古い entry — 一度読んで書き足し、次の実行から展開の text を読み直さない。
                cached = _to_cache(_from_cache(source, module, cached))
                store(cache_dir, key, cached)
            return _hit(source, relative, module, cached, key, stat)
        case CacheMiss():
            return _Miss(source, key, relative, module, stat)


def _hit(
    source: Path, relative: str, module: str, cached: CachedProjection, key: str, stat: FileStat | None
) -> _Hit:
    return _Hit(
        _from_cache(source, module, cached),
        frozenset(cached.imports or ()),
        relative,
        BundleRow(module, key, stat, cached),
    )


def _kept(cache_dir: Path | None, miss: _Miss, result: Projection | CompileFailure) -> BundleRow | None:
    """展開した結果を保存する(保存の書きはこの 1 か所 — 子の process は書かない)。展開に失敗した source は保存しない
    (直した時に作り直す・失敗の文言は展開の度に出す)。保存した展開の束の行を返す。"""
    if cache_dir is not None and miss.key is not None and isinstance(result, Projection):
        cached = _to_cache(result)
        store(cache_dir, miss.key, cached)
        return BundleRow(miss.module, miss.key, miss.stat, cached)
    return None


def project_cached(
    root: Path, roots: list[Path], source: Path, cache_dir: Path | None
) -> Projection | CompileFailure:
    """展開の cache(static_cache)を引き、無ければ展開して保存する。cache_dir が None なら毎回展開する。"""
    match _looked_up(root, roots, source, cache_dir):
        case _Hit(projection=projection):
            return projection
        case _Miss() as miss:
            result = project(root, roots, source)
            _kept(cache_dir, miss, result)
            return result


def _project_in_child(root: Path, roots: list[Path], source: Path) -> Projection | CompileFailure:
    """子の process で 1 つの source を展開する(pool に渡す関数 — module の直下に置き、子が import で引けるようにする)。"""
    return project(root, roots, source)


def _start_pool(workers: int) -> ProcessPoolExecutor:
    """依存の展開を並べる子の process の pool を起こす(forkserver — 親の thread を写さない)。"""
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("forkserver"))


class _Expanders:
    """保存から引けない依存の展開を受け持つ。引けない file が 2 つ以上の段だけ子の process の pool に並べ、pool は初めて要る時に
    起こして、1 回の実行の中で段をまたいで使い回す(全部引ける実行では起こさない — 保存が効く時の秒を増やさない)。"""

    def __init__(self, limit: int) -> None:
        # os.process_cpu_count(この process が使える CPU の数)は Python 3.13 から。3.12 までは機体の CPU の数で代える。
        cpus = os.process_cpu_count() if sys.version_info >= (3, 13) else os.cpu_count()
        self._workers = max(1, min(limit, cpus or 1))
        self._mut_pool: ProcessPoolExecutor | None = None

    def __enter__(self) -> "_Expanders":
        return self

    def __exit__(self, *_: object) -> None:
        if self._mut_pool is not None:
            self._mut_pool.shutdown(cancel_futures=True)

    def expanded(
        self, root: Path, roots: list[Path], sources: list[Path]
    ) -> dict[Path, Projection | CompileFailure]:
        """sources を展開する(1 つか、並べる子が 1 つなら親で順に)。子が落ちたら答えの無い file を名指して止める。"""
        if len(sources) < 2 or self._workers < 2:
            return {source: project(root, roots, source) for source in sources}
        if self._mut_pool is None:
            self._mut_pool = _start_pool(self._workers)
        pool = self._mut_pool
        futures = {source: pool.submit(_project_in_child, root, roots, source) for source in sources}
        try:
            return {source: future.result() for source, future in futures.items()}
        except BrokenProcessPool as error:
            unanswered = sorted(str(s) for s, f in futures.items() if not f.done() or f.exception() is not None)
            raise ExpansionWorkerLostError(
                "依存の展開を受け持った子の process が答えを返さずに落ちた: " + ", ".join(unanswered)
            ) from error


def project_closure(
    root: Path,
    roots: list[Path],
    targets: list[Path],
    cache_dir: Path | None,
    limit: int,
) -> Closure:
    """検める file と、それが import する根の下の .hy を全部展開する(cache_dir があれば変わらない物は引く)。

    import を段ごとに辿る(agora-redesign #3869): 段の中の file は互いの展開を使わないので、保存から引ける file は親で引き、
    引けない file はまとめて _Expanders に渡す(limit = 並べる子の process の数の上限・1 なら親で順に・既定は無い —
    呼び手が expander_limit(枠の正本が答えた数)で決める)。"""
    projections: dict[Path, Projection] = {}
    failures: list[CompileFailure] = []
    # 候補の path の有無は、実行の間に変わらない物として dir ごとに 1 度だけ引く(候補ごとに stat すると、10 file の検めで 10 万回になった)。
    listing = functools.cache(_listing)
    resolved = functools.cache(_present_candidates)
    seen: set[Path] = set(targets)
    wave = list(dict.fromkeys(targets))
    # closure 全体を 1 file に束ねた保存(温まった実行は束 1 つだけ読む)。束の行で引けない file は今までの引き方で引く。
    bundle_name = bundle_key(root, [str(t.relative_to(root)) for t in wave]) if cache_dir is not None else ""
    bundle = load_bundle(cache_dir, bundle_name) if cache_dir is not None else {}
    rows: dict[str, BundleRow] = {}
    # 展開の cache の「今の環境に合うか」の照らしは、file の状態を実行の間 1 度だけ stat する(照らしごとに覚えを捨てると、
    # 10 file の検めで stat が 10 万回・CPU 10 秒になった — card ki-0f0542a69a78)。
    with SharedScope(), _Expanders(limit) as expanders:
        while wave:
            looked = {source: _looked_up(root, roots, source, cache_dir, bundle) for source in wave}
            misses = [miss for miss in looked.values() if isinstance(miss, _Miss)]
            fresh = expanders.expanded(root, roots, [miss.source for miss in misses])
            for miss in misses:
                if (row := _kept(cache_dir, miss, fresh[miss.source])) is not None:
                    rows[miss.relative] = row
            found: list[Path] = []
            for source, lookup in looked.items():
                if isinstance(lookup, _Hit):
                    rows[lookup.relative] = lookup.row
                result = lookup.projection if isinstance(lookup, _Hit) else fresh[source]
                imports = lookup.imports if isinstance(lookup, _Hit) else None
                if isinstance(result, CompileFailure):
                    failures.append(result)
                    continue
                projections[source] = result
                # import の .hy は import の根と、import する file の dir から根までの親で探す(pyright の探し方 — search_dirs)。
                dirs = tuple(search_dirs(root, roots, source))
                for name in imports if imports is not None else imported_modules(result):
                    for absolute in resolved(dirs, name, listing):
                        if absolute not in seen:
                            seen.add(absolute)
                            found.append(absolute)
            wave = found
    if cache_dir is not None and bundle_signature(rows) != bundle_signature(bundle):
        store_bundle(cache_dir, bundle_name, rows)
    return Closure(projections, failures)


def _link_children(source: Path, target: Path) -> None:
    target.mkdir()
    for child in source.iterdir():
        if child.name not in {".git", "__pycache__"}:
            (target / child.name).symlink_to(child, target_is_directory=child.is_dir())


def _real_dir(root: Path, stage: Path, relative: Path) -> Path:
    """stage の中で relative の親 dir までを symlink でない実 dir にする(中身は symlink で映す)。"""
    current, original = stage, root
    for part in relative.parts[:-1]:
        current, original = current / part, original / part
        if current.is_symlink():
            current.unlink()
            _link_children(original, current)
    return stage / relative


def pyright_settings(root: Path) -> dict[str, object]:
    settings: dict[str, object] = {}
    declared = root / "pyrightconfig.json"
    if declared.is_file():
        loaded: object = json.loads(declared.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings = {str(k): v for k, v in loaded.items()}
    elif (root / "pyproject.toml").is_file() and sys.version_info >= (3, 11):
        import tomllib  # Python 3.11 から。3.10 では pyproject の [tool.pyright] を読まない

        table = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        tool = table.get("tool")
        pyright = tool.get("pyright") if isinstance(tool, dict) else None
        if isinstance(pyright, dict):
            settings = {str(k): v for k, v in pyright.items()}
    settings["reportDeprecated"] = "error"
    settings.setdefault("pythonVersion", f"{sys.version_info.major}.{sys.version_info.minor}")
    return settings


def _utf16_to_index(line: str, units: int) -> int:
    count = 0
    for index, char in enumerate(line):
        if count >= units:
            return index
        count += 2 if ord(char) > 0xFFFF else 1
    return len(line)


@dataclass(frozen=True)
class SpanIndex:
    """1 つの展開の位置の索引 — 診断ごとに全文を行へ割り直し、全部の範囲を頭から調べ直さないため(agora-redesign #2846:
    赤が 1,402 件の file で locate が 26 秒)。lines = 展開した Python の行・covering = 行の番号 → その行に掛かる範囲の
    番号(projection.spans の元の順)。範囲は始まりの行から終わりの行まで(終わりの行も含む)の全部の行に載る。"""

    spans: Sequence[Span]
    lines: tuple[str, ...]
    covering: tuple[tuple[int, ...], ...]


def span_index(projection: Projection) -> SpanIndex:
    """展開 1 つの位置の索引を 1 度だけ作る(locate を同じ展開の診断の数だけ呼ぶため)。"""
    rows = max((span.end[0] for span in projection.spans), default=-1) + 1
    # (行, 範囲の番号) の組を行・番号の順に並べ、行ごとにまとめる(番号の順 = projection.spans の元の順)。
    pairs = sorted(
        (row, number)
        for number, span in enumerate(projection.spans)
        for row in range(span.start[0], span.end[0] + 1)
    )
    by_row = {
        row: tuple(number for _, number in group)
        for row, group in groupby(pairs, key=itemgetter(0))
    }
    return SpanIndex(
        projection.spans,
        tuple(projection.text.split("\n")),
        tuple(by_row.get(row, ()) for row in range(rows)),
    )


def locate(index: SpanIndex, line: int, character: int) -> HyPosition:
    """展開した Python の位置(0 始まり)を、それを含む一番内側の node の Hy の位置へ。

    点を含む範囲はどれも点の行に掛かるので、その行に掛かる範囲だけを元の順で調べる(含まない範囲は元から選ばれないので、
    答えは全部の範囲を調べた時と同じ)。"""
    lines = index.lines
    column = _utf16_to_index(lines[line], character) if line < len(lines) else character
    point = (line, column)
    best: Span | None = None
    for number in index.covering[line] if 0 <= line < len(index.covering) else ():
        span = index.spans[number]
        contains = span.start <= point < span.end or span.start == point
        inner = best is None or (span.start >= best.start and span.end <= best.end)
        if contains and inner:
            best = span
    if best is None:
        return HyPosition(1, 1)
    return HyPosition(best.hy_line, best.hy_column)


def python_without_bytecode(directory: Path, python: str) -> Path:
    """pyright が検索 path を問うために起こす python が .pyc を書かないようにするため: python を `-B`(PYTHON* の環境変数を読まない
    起こし方の下でも効く)で起こす包みを directory に置く。pyright は呼び手の PYTHONDONTWRITEBYTECODE を効かせない形で起こすので、
    包まないと site の起動で .pth が読む module の .pyc を checkout へ書く(日次 2026-10-11 16:00 の doeff・card ki-04d762c59dac)。"""
    wrapper = directory / "python-without-bytecode"
    wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(python)} -B "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


#: pyright の thread の数(pyright の --threads — 検める file を子の process に分けて並べる)の既定。1 = --threads を渡さない
#: (1 本の pyright)。日次の型の測り(agora-controllers の hy-type-trend・.hy 2,551 本)は 1 本で約 2,250 秒かかり、処理ステージの
#: 上限に収まらなかった(10-12 12:00 JST の回・card ki-1475765a3f18)。子 1 本ごとに検める file の import 先を別に読むので、CPU の
#: 合計と memory は本数につれて増える(controllers/screen の 231 本で 2 本 = 秒 0.7 倍・CPU 1.2 倍、4 本 = 秒 0.5 倍・CPU 1.6 倍)。
#: だから既定は 1 のまま、呼び手が環境変数 PYRIGHT_THREADS_ENV(env_places が読み、入口の main だけが受ける)で機体に合わせて上げる。
PYRIGHT_THREADS = 1


def pyright_threads(configured: str | None, declared: int | None = None) -> int:
    """pyright の thread の数を決める(configured = 環境変数 PYRIGHT_THREADS_ENV の値・無ければ PYRIGHT_THREADS)。1 以上の整数で
    ない値は黙って既定へ落とさず断る(書き手の誤りを見えるようにする)。thread は pyright が起こす子の process なので、枠の正本が
    答えた 1 枠の中の並走の数(declared・届かなければ None)を越えない — 環境変数は数を下げる口で、宣言を越えて上げる口にしない。"""
    wanted = PYRIGHT_THREADS
    if configured is not None and configured.strip():
        text = configured.strip()
        if not text.isdigit() or int(text) < 1:
            raise ValueError(f"{PYRIGHT_THREADS_ENV} は 1 以上の整数: {configured!r}")
        wanted = int(text)
    return wanted if declared is None else min(wanted, declared)


def thread_clamp_note(configured: str | None, declared: int | None) -> str | None:
    """環境変数の指定が宣言の並走の数で頭打ちになる時の名乗りの 1 行(純粋・頭打ちでなければ None)。頭打ちは指定を無言で捨てる
    形だった — 日次の hy-type-trend が env を 4 にしても 4 回とも 2 本で走り、別の会話が原因を探し当てるまで見えなかった
    (agora-controllers 93c94c6f9・card ki-1bfe2c42dce6)。値の決め方は pyright_threads のまま、見えるようにするだけ。"""
    asked = pyright_threads(configured)
    used = pyright_threads(configured, declared)
    if used == asked:
        return None
    return (
        f"{PYRIGHT_THREADS_ENV}={asked} は機体の検の枠の宣言の並走の数 {declared} を越えるので pyright を {used} 本で撃つ"
        "(thread は pyright が起こす子の process — 宣言を越えて上げる口は無い)"
    )


def run_pyright(
    root: Path,
    settings: dict[str, object],
    projections: dict[Path, Projection],
    report: list[Path],
    pyright: str,
    python: str,
    threads: int = PYRIGHT_THREADS,
) -> Checked:
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="doeff-hy-check-") as temporary:
        stage = Path(temporary).resolve() / "repo"
        _link_children(root, stage)
        written: dict[Path, Projection] = {}
        for source, projection in projections.items():
            relative = source.relative_to(root).with_suffix(".py")
            if (root / relative).exists():
                notes.append(f"{relative}: 同じ名前の .py が在るので {source.name} を検められない")
                continue
            destination = _real_dir(root, stage, relative)
            destination.write_text(projection.text, encoding="utf-8")
            written[destination] = projection
        config = stage / "pyrightconfig.json"
        if config.is_symlink() or config.exists():
            config.unlink()
        config.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        targets = [str(dest) for dest, proj in written.items() if proj.source in report]
        if not targets:
            return Checked([], notes)
        searched = python_without_bytecode(Path(temporary).resolve(), python)
        spread = ["--threads", str(threads)] if threads > 1 else []
        completed = subprocess.run(
            [pyright, "--outputjson", *spread, "-p", str(config), "--pythonpath", str(searched), *targets],
            capture_output=True,
            text=True,
            check=False,
            cwd=stage,
        )
        try:
            output: object = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"pyright の出力を読めない(exit {completed.returncode}): "
                f"{completed.stdout[:400]}{completed.stderr[:400]}"
            ) from error
    diagnostics: list[Diagnostic] = []
    general = output.get("generalDiagnostics", []) if isinstance(output, dict) else []
    items = [
        item for item in (general if isinstance(general, list) else []) if isinstance(item, dict)
    ]
    # 位置の索引は、診断の在る展開ごとに 1 度だけ作る。
    named = {Path(str(item.get("file", ""))).resolve() for item in items}
    indexes = {
        destination: span_index(projection)
        for destination, projection in written.items()
        if destination in named
    }
    for item in items:
        destination = Path(str(item.get("file", ""))).resolve()
        projection = written.get(destination)
        if projection is None:
            continue
        start = item.get("range", {}).get("start", {})
        position = locate(
            indexes[destination], int(start.get("line", 0)), int(start.get("character", 0))
        )
        diagnostics.append(
            Diagnostic(
                str(projection.source.relative_to(root)),
                position.line,
                position.column,
                str(item.get("severity", "error")),
                str(item.get("rule", "")),
                str(item.get("message", "")).split("\n")[0],
            )
        )
    return Checked(diagnostics, notes)


#: pyright の診断を作って .hy の位置へ戻す関数(運びの指紋の材料 — どれかの source が替われば、前の回の診断を運ばない・
#: doeff_hy/static_carry.py の semantics_digest)。整え(settle)は運んだ診断にも毎回かけるので入れない。
CARRY_SEMANTICS = (
    Diagnostic,
    run_pyright,
    span_index,
    locate,
    _utf16_to_index,
    python_without_bytecode,
    _link_children,
    _real_dir,
)


def _rows_by_path(diagnostics: Iterable[Diagnostic]) -> tuple[FileRows, ...]:
    """pyright の診断を file ごとの運びの行にするため(file の中は pyright の答えの順のまま)。"""
    ordered = sorted(enumerate(diagnostics), key=lambda pair: (pair[1].path, pair[0]))
    return tuple(
        FileRows(path, tuple(CarriedRow(d.line, d.column, d.severity, d.rule, d.message) for _, d in group))
        for path, group in groupby(ordered, key=lambda pair: pair[1].path)
    )


def run_carried(
    root: Path,
    roots: list[Path],
    settings: dict[str, object],
    closure: Closure,
    report: list[Path],
    pyright: str,
    python: str,
    threads: int,
    carry: Path,
    refresh_runs: int,
) -> Checked:
    """運び(doeff_hy/static_carry.py)の下で pyright を撃つため: 前の回から入力の変わらない .hy は当てず、運びの file の前の回の診断を
    運ぶ。この回の運びで file を置き換え(書けなければ名乗って続ける — 次の回は前の運びと比べる)、当てた数と運んだ数を標準エラーに
    1 行出す。運びの file が読めない時は名乗って全部を当てる(運びは測りの近道で、測りを止める訳にしない)。"""
    try:
        previous: CarryRecord | CarryUnreadableError | None = read_carry(carry)
    except CarryUnreadableError as error:
        print(f"doeff-hy-check: {error} — 全部を当てる", file=sys.stderr)
        previous = error
    environment = probe_environment(settings, python, pyright, semantics_digest(CARRY_SEMANTICS), places_of(root))
    stub_dir = _absolute(root / str(settings.get("stubPath", "typings")))
    staged = {source: projection.text for source, projection in closure.projections.items()}
    graph = InputGraph(root, roots, stub_dir, environment, staged, [failure.source for failure in closure.failures])
    targets = [source for source in report if source in closure.projections]
    inputs = tuple(
        FileInputs(
            str(source.relative_to(root)),
            graph.inputs_of(
                source,
                own_digest(
                    source.read_text(encoding="utf-8"),
                    closure.projections[source].text,
                    closure.projections[source].spans,
                ),
            ),
        )
        for source in targets
    )
    plan = plan_carry(previous, environment.fingerprint, inputs, refresh_runs)
    chosen = plan.checked_paths
    checked = run_pyright(
        root,
        settings,
        closure.projections,
        [source for source in targets if str(source.relative_to(root)) in chosen],
        pyright,
        python,
        threads,
    )
    record, summary = next_record(plan, environment.fingerprint, inputs, _rows_by_path(checked.diagnostics))
    try:
        write_carry(carry, carry_text(record, summary))
    except OSError as error:
        print(f"doeff-hy-check: 運びの file {carry} を書けない({error})— 次の回は前の運びと比べる", file=sys.stderr)
    print(f"doeff-hy-check: {summary.line()}", file=sys.stderr)
    carried = [
        Diagnostic(kept.path, row.line, row.column, row.severity, row.rule, row.message)
        for kept in plan.carried
        for row in kept.rows
    ]
    return Checked([*checked.diagnostics, *carried], checked.notes)


def _checked(
    root: Path,
    roots: list[Path],
    settings: dict[str, object],
    closure: Closure,
    report: list[Path],
    args: argparse.Namespace,
    threads: int,
) -> Checked:
    """入口の引数のとおりに pyright を撃つため — --carry が在れば運びの下で(run_carried)、無ければ名指した .hy の全部に(run_pyright)。"""
    carry: Path | None = args.carry
    if carry is None:
        return run_pyright(root, settings, closure.projections, report, args.pyright, args.python, threads)
    return run_carried(
        root, roots, settings, closure, report, args.pyright, args.python, threads, carry, args.carry_refresh_runs
    )


def _hy_backed(module: str) -> bool:
    """module の実体が .hy か(pyright は .hy を source と見ないので、stub だけが見える)。"""
    relative = Path(*module.split("."))
    for entry in sys.path:
        base = Path(entry or ".") / relative
        if base.with_suffix(".hy").is_file() or (base / "__init__.hy").is_file():
            return True
    return False


_GUARD_MESSAGE = (
    "文の位置に置いた Program / effect は走らない — (<- _ ...) で束縛するか、本体の最後の式にする"
    " [ADR-DOE-HY-001]"
)
_UNKNOWN_TYPES = frozenset({"Unknown", "Any"})
#: 型検査のための展開が module の頭に置く補助の名(static_view.STATIC_HELPER_IMPORTS から読む — 名の並びを 2 か所に持たない)。
_STATIC_HELPER_NAMES: frozenset[str] = frozenset(
    _bound_name(alias)
    for statement in ast.parse(STATIC_HELPER_IMPORTS).body
    if isinstance(statement, ast.ImportFrom)
    for alias in statement.names
)


def _revealed(diagnostic: Diagnostic) -> str | None:
    """`reveal_type` の情報(`Type of "x" is "T"`)なら T。"""
    if diagnostic.severity != "information" or not diagnostic.message.startswith("Type of "):
        return None
    quoted = diagnostic.message.rsplit('"', 2)
    return quoted[-2] if len(quoted) == 3 else None


def settle(diagnostics: list[Diagnostic]) -> Checked:
    """展開の都合で出る物を整える。返すのは (診断, 注記)。

    - 同じ診断を 1 つにする: macro が同じ利用者の式を 2 か所へ写すと、その式の中の赤が
      同じ Hy の位置で 2 回出る。
    - .hy が実体の module: pyright は .hy を source と見ないので「source が見つからない」
      警告は落とし、「解決できない」赤は注記にする(その module の型は見えない)。
    - 文の位置の Program / effect(macros.pyi の deprecated の overload): 同じ位置の
      `reveal_type` が Unknown / Any なら落とす(型の分からない値を決めつけない)。
      残した物は文言を置き換える。`reveal_type` の情報そのものは出さない。
    """
    revealed: dict[tuple[str, int, int], str] = {}
    for diagnostic in diagnostics:
        shown = _revealed(diagnostic)
        if shown is not None:
            revealed[(diagnostic.path, diagnostic.line, diagnostic.column)] = shown
    kept: list[Diagnostic] = []
    notes: list[str] = []
    seen: set[Diagnostic] = set()
    for diagnostic in diagnostics:
        if diagnostic in seen or _revealed(diagnostic) is not None:
            continue
        seen.add(diagnostic)
        quoted = diagnostic.message.split('"')
        module = quoted[1] if len(quoted) >= 2 else ""
        if diagnostic.rule == "reportMissingModuleSource" and _hy_backed(module):
            continue
        # 補助の名(_doeff_do・_doeff_perform ほか)は doeff-hy が展開に置く名で、利用者は書かない — strict の
        # 「private な名を module の外で使った」は展開の都合なので出さない。
        if diagnostic.rule == "reportPrivateUsage" and module in _STATIC_HELPER_NAMES:
            continue
        if diagnostic.rule == "reportMissingImports" and _hy_backed(module):
            notes.append(f"{diagnostic.path}:{diagnostic.line}: {module} は .hy なので型が見えない")
            continue
        if diagnostic.rule == "reportDeprecated" and "_guard_statement_value" in diagnostic.message:
            key = (diagnostic.path, diagnostic.line, diagnostic.column)
            if revealed.get(key, "Unknown") in _UNKNOWN_TYPES:
                continue
            kept.append(
                Diagnostic(
                    diagnostic.path,
                    diagnostic.line,
                    diagnostic.column,
                    diagnostic.severity,
                    "doeff-hy-unperformed",
                    f"{_GUARD_MESSAGE}(値の型: {revealed[key]})",
                )
            )
            continue
        kept.append(diagnostic)
    return Checked(kept, list(dict.fromkeys(notes)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doeff-hy-check", description="Hy の source を展開して pyright で型を検める"
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pyright", default="pyright")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--import-root",
        action="append",
        default=[],
        help="根の下の import の根を足す(繰り返せる・pyright の extraPaths にも足す)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="基点の赤の file(--write-baseline で書いた物)。基点に無い赤だけを exit 1 にする",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        help="この実行の赤を基点の file として書き出す(書いた時は exit 0)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="展開の cache の置き場(既定 = $XDG_CACHE_HOME/doeff-hy-check か ~/.cache/doeff-hy-check)",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="展開の cache を使わず毎回全部展開する"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="pyright の typeCheckingMode を strict にする(根の設定より優先)",
    )
    parser.add_argument(
        "--carry",
        type=Path,
        default=None,
        help="運びの file(前の回の診断と入力の digest)。入力の変わらない .hy は当てずに前の回の診断を運び、この回の物で置き換える",
    )
    parser.add_argument(
        "--carry-refresh-runs",
        type=int,
        default=DEFAULT_REFRESH_RUNS,
        help="入力の変わらない .hy も、この回数のうちに 1 度は当て直す(0 = 当て直さない)",
    )
    args = parser.parse_args(argv)
    try:
        baseline = read_baseline(args.baseline) if args.baseline else None
    except BaselineUnreadable as error:
        print(f"doeff-hy-check: 走れなかった: {error}", file=sys.stderr)
        return 2
    root: Path = _absolute(args.root)
    settings = pyright_settings(root)
    if args.strict:
        settings["typeCheckingMode"] = "strict"
    if args.import_root:
        present = settings.get("extraPaths")
        extra = [str(e) for e in present] if isinstance(present, list) else []
        settings["extraPaths"] = [*extra, *(e for e in args.import_root if e not in extra)]
    roots = import_roots(root, settings)
    for entry in reversed(roots):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    # 名指した path は根の綴りへ写す。根の外の path は黙って捨てず断る — 捨てると赤の在る file で 0 件と答える(card ki-ed254bf53094)。
    named = [(p, in_root(root, p if p.is_absolute() else Path.cwd() / p)) for p in (args.paths or [root])]
    outside = [str(p) for p, placed in named if placed is None]
    if outside:
        print(f"doeff-hy-check: 走れなかった: 名指した path が根の外(根 = {root}): {'・'.join(outside)}", file=sys.stderr)
        return 2
    report = hy_files([placed for _, placed in named if placed is not None])
    # 展開と pyright の間は機体の検の枠を 1 つ持つ — 撃つ道(会話の直・gate の子・日次・pytest の中)を問わずここで取る(doeff_hy.check_slot)。
    slot = take_slot(Path.home(), slot_label(root, conversation_setting()), sys.stderr)
    if isinstance(slot, SlotExpired):
        print(f"doeff-hy-check: 走れなかった: {slot.line}", file=sys.stderr)
        return 2
    try:
        cache_dir = None if args.no_cache else (args.cache_dir or default_cache_dir())
        threads = pyright_threads(pyright_threads_setting(), slot.width)  # 展開の前に判じる(誤った値で展開の秒を払わない)
        if (clamped := thread_clamp_note(pyright_threads_setting(), slot.width)) is not None:
            print(f"doeff-hy-check: {clamped}", file=sys.stderr)
        closure = project_closure(root, roots, report, cache_dir, expander_limit(slot.width))
        checked = _checked(root, roots, settings, closure, report, args, threads)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"doeff-hy-check: 走れなかった: {error}", file=sys.stderr)
        return 2
    finally:
        slot.release()
    compile_errors = [f.diagnostic for f in closure.failures if f.source in report]
    # val / var の検査の所見(setv の使用 = 警告・束縛し直し = 赤・旧い lazy = 警告 — ADR-DOE-HY-006)。
    binding_findings = [
        d for p in closure.projections.values() if p.source in report for d in p.findings
    ]
    settled = settle(
        sorted(
            compile_errors + binding_findings + checked.diagnostics,
            key=lambda d: (d.path, d.line, d.column),
        )
    )
    diagnostics = settled.diagnostics
    notes = checked.notes + settled.notes
    if args.write_baseline:
        args.write_baseline.write_text(baseline_json(list(diagnostics)), encoding="utf-8")
        print(
            f"doeff-hy-check: 基点を {args.write_baseline} に書いた(error {len(errors_of(diagnostics))} 件)",
            file=sys.stderr,
        )
        return 0
    verdict = split(baseline, diagnostics) if baseline is not None else Split((), tuple(errors_of(diagnostics)))
    known = {id(d) for d in verdict.known}
    if args.json:
        print(
            json.dumps(
                [vars(d) | {"known": id(d) in known} for d in diagnostics], ensure_ascii=False, indent=1
            )
        )
    else:
        for diagnostic in diagnostics:
            if id(diagnostic) not in known:
                print(diagnostic.render())
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        warnings = sum(1 for d in diagnostics if d.severity == "warning")
        print(
            f"{len(report)} 個の .hy を検めた: 新しい error {len(verdict.new)} 件・"
            f"基点に在る error {len(verdict.known)} 件・warning {warnings} 件",
            file=sys.stderr,
        )
    return 1 if verdict.new else 0


if __name__ == "__main__":
    raise SystemExit(main())
