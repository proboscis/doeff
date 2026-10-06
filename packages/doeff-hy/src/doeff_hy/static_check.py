"""doeff-hy-check — Hy の source を本物の macro で展開し、pyright で型を検め、赤を .hy の行で返す。

    doeff-hy-check [PATH ...] [--root DIR] [--import-root DIR ...] [--pyright CMD] [--python EXE] [--json]
                   [--strict] [--baseline JSON | --write-baseline JSON] [--cache-dir DIR | --no-cache]

- PATH: 検める .hy の file か dir(既定 = --root)。dir は下の .hy を全部。
- --root: repo の根(既定 = 今の dir)。import の根で、macro の `require` もここから解く。
- --import-root: 根の下の別の import の根(例 `clients/hy`)。根の pyright の設定の
  `extraPaths` も import の根として読む。
- --strict: pyright の typeCheckingMode を strict にする。
- --write-baseline / --baseline: 今の赤を基点として書く / 基点に無い赤だけを止める(doeff_hy/static_baseline.py)。
  --json の診断には、基点に在る赤かを欄 `known` で付ける。
- --cache-dir / --no-cache: 依存の .hy の展開を、source と展開が通った file の記録ごとに保存して引く(doeff_hy/static_cache.py・
  既定の置き場 = $XDG_CACHE_HOME/doeff-hy-check か ~/.cache/doeff-hy-check)。
- 新しい赤(基点が無ければ全部の赤)があれば exit 1、無ければ 0、道具として走れなければ 2。

仕組み:
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
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import hy
from doeff_hy_bytecode_guard import current_record
from hy.compiler import hy_compile
from hy.errors import HyLanguageError

from doeff_hy.binding_forms import Finding, module_findings
from doeff_hy.static_baseline import (
    BaselineUnreadable,
    Split,
    baseline_json,
    errors_of,
    read_baseline,
    split,
)
from doeff_hy.static_cache import (
    CachedFinding,
    CachedProjection,
    CachedSpan,
    CacheMiss,
    default_cache_dir,
    load,
    place,
    store,
)
from doeff_hy.static_view import STATIC_HELPER_IMPORTS, collect_findings, static_view

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


@dataclass(frozen=True)
class Span:
    """展開した Python の範囲(0 始まりの行・列、終わりは含まない)と、対応する Hy の位置(1 始まり)。"""

    start: tuple[int, int]
    end: tuple[int, int]
    hy_line: int
    hy_column: int


@dataclass(frozen=True)
class Projection:
    source: Path
    module: str
    text: str
    spans: tuple[Span, ...]
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


def _doeff_hy_import(statement: ast.stmt) -> bool:
    """doeff-hy 自身の module の import か(macro が定義ごとに合成する物 — 利用者の重複の import は赤のまま残すため、
    重ねを 1 つにするのはこれだけ)。"""
    match statement:
        case ast.Import(names=names):
            return all(alias.name.split(".")[0] == "doeff_hy" for alias in names)
        case ast.ImportFrom(module=str(module), level=0):
            return module.split(".")[0] == "doeff_hy"
        case _:
            return False


def without_bookkeeping(tree: ast.Module) -> ast.Module:
    """型検査のための展開から、型の意味を持たない記帳を外す(pyright strict の誤検出の元を展開の側で絶つ)。

    - 定義の記帳と `hy.macros.require` の残り(_bookkeeping_statement)を外す。
    - module の直下で同じ doeff-hy の import の文が繰り返されたら最初の 1 つだけを残す(macro が定義ごとに出す import)。
    - 記帳だけが使う module(BOOKKEEPING_MODULES)の import の名(1 つの文に並んだ名も 1 つずつ)と Hy の `import hy` で、
      もう読まれない物を外す。
    消費 repo の利用者が書いた式は外さない(外すのは macro が合成した文の形だけ)。"""
    kept = [statement for statement in tree.body if not _bookkeeping_statement(statement)]
    imports = (ast.Import, ast.ImportFrom)
    seen_keys = [ast.dump(s) if _doeff_hy_import(s) else None for s in kept]
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
    try:
        with static_view(), collect_findings() as found:
            compiled = hy_compile(
                hy.read_many(text, filename=str(source)), module, filename=str(source), source=text
            )
        # hy_compile は get_expr=True の時だけ (Module, Expression) の組を返す。
        if not isinstance(compiled, ast.Module):
            raise TypeError(f"hy_compile が module を返さなかった: {type(compiled).__name__}")
        tree = with_static_helpers(without_bookkeeping(compiled))
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
    # module の直下の setv は macro の外なので、source の一番外の並びを別に読む(ADR-DOE-HY-006)。
    top_level = module_findings(hy.read_many(text, filename=str(source)))
    return Projection(
        source,
        name,
        rendered,
        tuple(spans),
        tuple(_finding_diagnostic(relative, f) for f in (*found, *top_level)),
        used=current_record(module, str(source), also=(sys.modules[__name__],)),
    )


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


def _module_candidates(dirs: list[Path], name: str) -> list[Path]:
    """import の名 name が dirs の下で当たりうる .hy(dir ごとに <名>.hy と <名>/__init__.hy)— 依存の展開が探す file。"""
    found: list[Path] = []
    for directory in dirs:
        base = directory.joinpath(*name.split("."))
        found += [base.with_suffix(".hy"), base / "__init__.hy"]
    return found


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
        tuple(Span(s.start, s.end, s.hy_line, s.hy_column) for s in cached.spans),
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
        tuple(CachedSpan(s.start, s.end, s.hy_line, s.hy_column) for s in projection.spans),
        tuple(
            CachedFinding(f.path, f.line, f.column, f.severity, f.rule, f.message)
            for f in projection.findings
        ),
        projection.used,
    )


def project_cached(
    root: Path, roots: list[Path], source: Path, cache_dir: Path | None
) -> Projection | CompileFailure:
    """展開の cache(static_cache)を引き、無ければ展開して保存する。cache_dir が None なら毎回展開する。

    展開に失敗した source は保存しない(直した時に作り直す・失敗の文言は展開の度に出す)。"""
    if cache_dir is None:
        return project(root, roots, source)
    module = module_name(roots, source)
    key = place(source.read_text(encoding="utf-8"), module, str(source.relative_to(root)))
    match load(cache_dir, key):
        case CachedProjection() as cached:
            return _from_cache(source, module, cached)
        case CacheMiss():
            result = project(root, roots, source)
            if isinstance(result, Projection):
                store(cache_dir, key, _to_cache(result))
            return result


def project_closure(
    root: Path, roots: list[Path], targets: list[Path], cache_dir: Path | None = None
) -> Closure:
    """検める file と、それが import する根の下の .hy を全部展開する(cache_dir があれば変わらない物は引く)。"""
    projections: dict[Path, Projection] = {}
    failures: list[CompileFailure] = []
    pending = list(targets)
    seen: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        result = project_cached(root, roots, source, cache_dir)
        if isinstance(result, CompileFailure):
            failures.append(result)
            continue
        projections[source] = result
        # import の .hy は import の根と、import する file の dir から根までの親で探す(pyright の探し方 — search_dirs)。
        dirs = search_dirs(root, roots, source)
        for name in imported_modules(result):
            for candidate in _module_candidates(dirs, name):
                if candidate.is_file() and _absolute(candidate) not in seen:
                    pending.append(_absolute(candidate))
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

    spans: tuple[Span, ...]
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


def run_pyright(
    root: Path,
    settings: dict[str, object],
    projections: dict[Path, Projection],
    report: list[Path],
    pyright: str,
    python: str,
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
        completed = subprocess.run(
            [pyright, "--outputjson", "-p", str(config), "--pythonpath", python, *targets],
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
    report = hy_files([p if p.is_absolute() else Path.cwd() / p for p in (args.paths or [root])])
    report = [path for path in report if path.is_relative_to(root)]
    try:
        cache_dir = None if args.no_cache else (args.cache_dir or default_cache_dir())
        closure = project_closure(root, roots, report, cache_dir)
        checked = run_pyright(
            root, settings, closure.projections, report, args.pyright, args.python
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"doeff-hy-check: 走れなかった: {error}", file=sys.stderr)
        return 2
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
