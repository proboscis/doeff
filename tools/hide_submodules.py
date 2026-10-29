"""subpackage の submodule を ``_`` の名へ移し、古い path を読む import を入口か ``_`` の path へ書き換える道具。

決まり: ADR-DOE-API-001 の R5 — 公開の名は subpackage の import の根(``__init__``)からだけ出し、根のすぐ下の子
(module か package)は名に ``_`` を付けて隠す。doeff の main を兄弟の dir から読む外の repo(agora-controllers・
merge-queue・moneta・custody)を壊さない様に、3 つの stage で進める(card ki-83e663b21128 と同じ順):

1. ``hide``(doeff の木): 子を ``git mv`` で ``_<名>`` へ移し、元の名の所に移し替えの module を置き、子の名のうち入口へ
   出す名を入口の遅延の名に足し、doeff の木の import を書き換える。
2. ``rewrite``(外の repo の作業木): 隠した後の package に合わせて、その木の import を書き換える。
3. ``finish``(doeff の木): 移し替えの module を消す。

使い方(doeff の作業木で)::

    uv run python tools/hide_submodules.py hide --package packages/doeff-agents --child tmux --child adapters
    uv run python tools/hide_submodules.py rewrite --package packages/doeff-agents --child tmux \\
        --tree ~/.worktrees/agora-controllers-wt-x
    uv run python tools/hide_submodules.py finish --package packages/doeff-agents --child tmux

入力:

- ``--package``: package の dir(doeff の木の ``packages/<名>``)。import の根は ``tools/public_api_inventory.py`` が
  ``pyproject.toml`` から読む。
- ``--child``(何度でも): 子の名。import の根のすぐ下の module(``.py``・``.hy`` と、隣の ``.pyi``)か package の dir。
  ``rewrite`` と ``finish`` では ``_<名>`` へ移した後の子を、移す前の名で渡す。
- ``--tree``: 書き換える木(git の作業木)。``hide`` と ``finish`` の既定は package を持つ git の木。
- ``--table``(``hide``): 既知の表(既定 ``tests/data/public_api_known_violations.json``)。名の行き先を決める。
- ``--underscore-only``(何度でも): import の根の外で、入口でなく ``_`` の path だけを読む file か dir(木からの相対)。
  読み込みの順の固い所(例: doeff-core-effects を隠す時の ``doeff``)に使う。
- ``--json``: 結果を JSON で書く file(``-`` は stdout)。``--dry-run``(``rewrite``): 書かずに数だけ出す。

名の行き先(``hide``): 目録(``public_api_inventory``)が子とその下の module から読む名を、種類と既知の表の組で分ける。

- 入口へ出す: 種類 effect・handler、表の組「境界 — …」(R2 の型と答えの出ていない境界の名)と「未分け — …」、「足した名」。
- ``_`` の中に残す: 表の組「外れ — …」の名(R1 の「内側の module へ移す」)と、表に無いそれ以外の名(注に出す)。
- Hy の macro は入口の遅延の名にできないので ``_`` に残し、``require`` は ``_`` の path を読む(注に出す)。
- 同じ名で定義の違う物が 2 つ在る(子どうし・入口に在る名と)時は足さずに「ぶつかり」に出す。入口に同じ定義が在れば足さない。
  子が別の子の名を import して出し直す(定義が 1 つ)時は、定義の在る子から 1 度だけ足す。

入口は遅延の import で書く: ``_LAZY_EXPORTS``(名から module へ)・``__getattr__``・``__dir__``・``if TYPE_CHECKING:`` の
``X as X``(``.py`` か ``.pyi`` の在る module の名だけ。``__all__`` は使わない — DOEFF021)。入口に ``_LAZY_EXPORTS`` と
``__getattr__`` が在れば表に行を足し、無ければ形を末尾に足す。入口の無い名前空間 package には入口を作る。入口に
``__all__`` が既に在れば、足した名を ``__all__`` にも足す(目録と ``import *`` は ``__all__`` の名だけを読む)。

移し替えの module(``<根>/<子>.py`` の 1 file・1 行目に ``# moved-submodule-shim: <古い path> -> <新しい path>``)は、
import すると ``_<子>`` の module そのものを返し(``sys.modules`` を差し替える)、子が package なら下の module も meta path の
finder が ``_<子>.<下>`` の module そのものを返す。古い path の ``mock.patch`` の的・isinstance・``python -m <古い path>`` は
移す前と同じに効く。

書き換え(木の git の追跡する file):

- Python(``.py``・``.pyi``): ``from`` と ``import`` の文・相対 import・``from <根> import <子>``(``_<子> as <子>`` —
  入口が同じ名で子の module でない物を出す時は入口の物を読むので書き換えない)・
  文字列(``mock.patch`` の的・``-m`` の module)・code の名・comment の module path。
- Hy(``.hy``): ``(import …)``・``(require …)``・文字列・code の名(``-`` で書いた名も読む)。
- markdown(``.md``): code の囲み(言語ごとに Python か Hy の書き換え)と行の中の code。地の文は書き換えず「残った古い path」に数える。
- 設定(``.toml``・``.yaml``・``.json`` ほか): module path(``pyproject`` の entry point の的など)と file の path。
- file の path(``<根>/<子>``)はどの file でも ``<根>/_<子>`` へ。

import の根の外の file(呼び手)は、名が入口から読めて入口の名と同じ物なら入口から(``from <根> import X``)、そうでなければ
``_`` の path から読む。入口と同じ物かは、package を ``sys.path`` の先頭に置いた子の process が import して確かめる。
import の根の中の file と ``--underscore-only`` は ``_`` の path だけを読む。Hy の ``require`` はいつも ``_`` の path。

書き換えない file: 設計の記録(``pyproject.toml`` の ``[tool.doeff] record-dirs`` と ``docs/design/``)・``docs/history/``・
``publications/``・``evidence`` の dir・``CHANGELOG.md``(歴史)、既知の表(表を直す card が扱う)、移し替えの module。

出力(stdout と ``--json``): 前と後の数 — 子・名(行き先ごと)・古い path を読む場所(前)・書き換えた場所(種類ごと)・
入口へ向けた名と ``_`` の path へ向けた名(うち表の外れの名)・残った古い path(file ごと)・ぶつかりと注。

``finish`` は移し替えの module を ``git rm`` で消し、木に残った古い path を数え、消した後の目録と既知の表を表の test
(``tests/test_public_api_known_violations.py``)と同じ突き合わせで比べて、隠した子の消えた行(隠した submodule の行・
``_`` に残した外れの名の行)を表から消す。入口へ移した名の行は残る(表の test が入口の名と同じ行と読む)。
``hide`` の間は表を書き換えない(移し替えの module が古い名で名を出し、目録が古い名で数える)。

stage ごとに数える物(card の発言に写す):

- ``hide``: 子の数と module の数・名(行き先ごと)・入口へ足した名・古い path を読む場所(前)・書き換えた場所(後)・
  入口へ向けた名と ``_`` の path へ向けた名(うち外れ)・残った古い path・ぶつかり・注。
- ``rewrite``: 子・古い path を読む場所(前)・書き換えた場所(後)・入口と ``_`` の path へ向けた名・残った古い path。
- ``finish``: 消した移し替えの module・残った古い path・表から消した行と残した行(組ごと)。
"""

from __future__ import annotations

import argparse
import ast
import collections
import dataclasses
import importlib
import importlib.util
import inspect
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import types
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Literal, Protocol, TypedDict, cast

import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE = Path("tests/data/public_api_known_violations.json")
SHIM_MARK = "# moved-submodule-shim:"
LINE_LIMIT = 100

#: 歴史として書き換えない所(木の根からの相対の頭・dir の名・file の名)。
HISTORY_PREFIXES = ("docs/design/", "docs/design-checks/", "docs/history/", "publications/")
HISTORY_DIR_NAMES = frozenset({"evidence"})
HISTORY_FILE_NAMES = frozenset({"CHANGELOG.md"})

PYTHON_SUFFIXES = (".py", ".pyi")
HY_SUFFIXES = (".hy",)
MARKDOWN_SUFFIXES = (".md",)
CONFIG_SUFFIXES = (".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".sh", ".txt", ".rst")
CONFIG_NAMES = frozenset({"Makefile", "Dockerfile"})
_PYTHON_FENCES = frozenset({"python", "py", "python3", "pycon", "ipython"})
_HY_FENCES = frozenset({"hy", "hylang", "clojure", "clj", "lisp"})

# 書き換えた場所の種類(数の鍵)。
PY_IMPORT = "Python の import"
HY_IMPORT = "Hy の import"
HY_REQUIRE = "Hy の require"
STRING = "文字列の module path"
CODE = "code の名"
COMMENT = "comment"
MARKDOWN = "markdown の code"
CONFIG = "設定の module path"
FILE_PATH = "file の path"

Fate = Literal["entry", "already", "underscore", "macro", "conflict"]
FATE_LABELS: dict[Fate, str] = {
    "entry": "入口へ足す",
    "already": "入口に既に在る",
    "underscore": "_ に残す",
    "macro": "Hy の macro(_ に残す)",
    "conflict": "ぶつかり(足さない)",
}


class _Inventory(Protocol):
    """``tools/public_api_inventory.py`` のうち、この道具が使う所。"""

    def read_layout(self, package_dir: Path) -> _Layout: ...

    def inventory(
        self, repo: Path, packages: Sequence[str] | None, jobs: int, timeout: float
    ) -> list[dict[str, object]]: ...

    @property
    def SUBMODULE(self) -> str: ...  # noqa: N802 - the inventory's constant


class _Root(Protocol):
    name: str
    path: Path


class _Layout(Protocol):
    dir: Path
    roots: tuple[_Root, ...]
    maturin: bool


def _inventory_tool() -> _Inventory:
    """目録の道具を読む(tools/ は package でない — 表の検と同じ名で 1 つを共にする)。"""
    loaded = sys.modules.get("public_api_inventory")
    if loaded is None:
        spec = importlib.util.spec_from_file_location(
            "public_api_inventory", Path(__file__).with_name("public_api_inventory.py")
        )
        assert spec is not None
        assert spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        sys.modules["public_api_inventory"] = loaded
        spec.loader.exec_module(loaded)
    return cast(_Inventory, loaded)


# --- 子 ---


@dataclass(frozen=True)
class Child:
    """隠す子 1 つ: import の根の名・子の名・根の dir。"""

    root: str
    name: str
    root_dir: Path

    @property
    def old(self) -> str:
        return f"{self.root}.{self.name}"

    @property
    def new(self) -> str:
        return f"{self.root}._{self.name}"

    @property
    def shim(self) -> Path:
        return self.root_dir / f"{self.name}.py"

    def sources(self, stem: str) -> list[Path]:
        """子の file と dir(``stem`` は子の名か ``_`` の名)。"""
        candidates = [
            self.root_dir / stem,
            *(self.root_dir / f"{stem}{s}" for s in (".py", ".hy", ".pyi")),
        ]
        return [path for path in candidates if path.exists()]

    def is_package(self) -> bool:
        return (self.root_dir / f"_{self.name}").is_dir() or (self.root_dir / self.name).is_dir()

    def shadowed_by_entry(self) -> bool:
        """入口(根の ``__init__.py``)が子と同じ名で、子の module でない物を出すか。

        例: 子 ``structured_llm`` と、入口の ``from ._structured_llm import structured_llm`` の関数。その時
        ``from <根> import <子>`` は入口の物を読むので、``_<子> as <子>`` へ書き換えない。
        """
        return _entry_binds_other(self.root_dir / "__init__.py", self.root, self.name)


def _entry_binds_other(entry: Path, root: str, name: str) -> bool:
    """``entry`` の top level が ``name`` を、子の module(``from . import <名>``・``_<名> as <名>``)でない物に束ねるか。"""
    if not entry.is_file():
        return False
    tree = ast.parse(entry.read_text(encoding="utf-8"))
    return any(_binds_other(node, root, name) for node in tree.body)


def _binds_other(node: ast.stmt, root: str, name: str) -> bool:
    """入口の文 1 つが ``name`` を子の module でない物に束ねるか(呼び手の ``from <根> import <名>`` を残す為)。"""
    # match の文で書かない: commit の hook の semgrep が、この file の match の文を構文として読めない。
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if "_LAZY_EXPORTS" in names and isinstance(node.value, ast.Dict):
            return any(isinstance(k, ast.Constant) and k.value == name for k in node.value.keys)
        return name in names
    if isinstance(node, ast.ImportFrom):
        own = (node.level == 1 and node.module is None) or (node.level == 0 and node.module == root)
        return any(
            (alias.asname or alias.name) == name and not (own and alias.name in (name, f"_{name}"))
            for alias in node.names
        )
    return False


def _check_child_name(name: str) -> None:
    if not name.isidentifier() or name.startswith("_"):
        raise SystemExit(f"子の名 {name!r} は _ で始まらない Python の名にする")


def _find_child(layout: _Layout, name: str, *, hidden: bool) -> Child:
    """子を持つ import の根を 1 つ探す(``hidden`` は ``_`` へ移した後の子を探す)。"""
    _check_child_name(name)
    stem = f"_{name}" if hidden else name
    found = [
        Child(root.name, name, root.path)
        for root in layout.roots
        if Child(root.name, name, root.path).sources(stem)
    ]
    if len(found) != 1:
        where = ", ".join(str(root.path) for root in layout.roots) or "(import の根が無い)"
        state = "移した後の _" if hidden else "移す前の"
        raise SystemExit(
            f"{state}子 {stem} が import の根({where})に 1 つだけ在る形でない: {len(found)} 個"
        )
    child = found[0]
    extensions = [p for p in child.root_dir.glob(f"{stem}.*") if p.suffix in (".so", ".pyd")]
    if extensions:
        raise SystemExit(f"{child.old}: 組み立てた module({extensions[0].name})は移さない")
    return child


def find_children(layout: _Layout, names: Sequence[str], *, hidden: bool) -> list[Child]:
    """名ごとに、子を持つ import の根を 1 つ探す。"""
    return [_find_child(layout, name, hidden=hidden) for name in names]


# --- 書き換えの型(path の正規表現) ---


def _flex(word: str, hy: bool) -> str:
    """名の正規表現。Hy では ``_`` を ``-`` で書いた名も読む。"""
    return "[-_]".join(re.escape(part) for part in word.split("_")) if hy else re.escape(word)


def _mangle_plain(text: str) -> str:
    return text.replace("-", "_")


@dataclass(frozen=True)
class Patterns:
    """隠す子の古い path を探す正規表現(Python の名・Hy の名・file の path)。"""

    children: tuple[Child, ...]
    dotted: re.Pattern[str]
    dotted_hy: re.Pattern[str]
    path: re.Pattern[str]

    @classmethod
    def of(cls, children: Sequence[Child]) -> Patterns:
        def build(hy: bool, separator: str) -> re.Pattern[str]:
            roots = "|".join(sorted({_flex(c.root, hy) for c in children}, key=len, reverse=True))
            names = "|".join(sorted({_flex(c.name, hy) for c in children}, key=len, reverse=True))
            return re.compile(
                rf"(?<![\w.\-])(?P<root>{roots}){separator}(?P<child>{names})(?![\w\-])"
            )

        return cls(tuple(children), build(False, r"\."), build(True, r"\."), build(False, "/"))

    def hidden_names(self, root: str) -> set[str]:
        """``from <根> import <子>`` で ``_<子> as <子>`` へ書き換える子の名(入口が同じ名で別の物を出す子を除く)。"""
        return {c.name for c in self.children if c.root == root and not c.shadowed_by_entry()}

    def child_of(self, root_text: str, child_text: str) -> Child | None:
        key = (_mangle_plain(root_text), _mangle_plain(child_text))
        return next((c for c in self.children if (c.root, c.name) == key), None)

    def tokens(self) -> tuple[str, ...]:
        """木から候補の file を引く固定の語(根の名と、``-`` で書いた根の名)。"""
        return tuple(
            sorted(
                {c.root for c in self.children} | {c.root.replace("_", "-") for c in self.children}
            )
        )


# --- 場所の種類(元の text の中の範囲) ---


@dataclass(frozen=True)
class Span:
    """元の text の範囲 1 つと、その場所の種類。"""

    start: int
    end: int
    label: str


@dataclass(frozen=True)
class Spans:
    """元の text の範囲ごとの種類。重なれば前に在る物が勝つ。"""

    items: tuple[Span, ...] = ()

    def label_at(self, position: int, default: str) -> str:
        return next((s.label for s in self.items if s.start <= position < s.end), default)


def _line_starts(text: str) -> list[int]:
    return [0, *(match.end() for match in re.finditer("\n", text))]


def _offset(text: str, starts: list[int], line: int, byte_col: int) -> int:
    """ast の (行, byte の列) を text の字の位置へ。"""
    begin = starts[line - 1]
    end = starts[line] if line < len(starts) else len(text)
    return begin + len(text[begin:end].encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))


@dataclass
class Counts:
    """1 file か木の数: 書き換えた場所(種類ごと)と、入口と ``_`` の path へ向けた名。"""

    places: collections.Counter[str] = field(default_factory=collections.Counter)
    to_entry: collections.Counter[tuple[str, str]] = field(default_factory=collections.Counter)
    to_underscore: collections.Counter[tuple[str, str]] = field(default_factory=collections.Counter)
    #: 字句か構文を読めず、module path と file の path だけを書き換えた text の数(import の文は分けていない)。
    unread: int = 0

    def merge(self, other: Counts, relabel: str | None = None) -> None:
        """他の数を足す(markdown の code の囲みは ``relabel`` の 1 つの種類に数え、読めない囲みは数えない)。"""
        self.places.update(
            collections.Counter({relabel or label: count for label, count in other.places.items()})
            if relabel is None
            else collections.Counter({relabel: other.places.total()})
        )
        self.to_entry.update(other.to_entry)
        self.to_underscore.update(other.to_underscore)
        self.unread += 0 if relabel else other.unread


Decide = Callable[[str, str], bool]


@dataclass(frozen=True)
class FileContext:
    """書き換える file 1 つ: 木からの相対の path・import の根の中なら module の名・``_`` の path だけを読むか。"""

    relative: str
    module: str | None
    underscore_only: bool


def _sub(
    pattern: re.Pattern[str],
    text: str,
    patterns: Patterns,
    counts: Counts,
    label: Callable[[int], str],
    separator: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        child = patterns.child_of(match["root"], match["child"])
        if child is None:
            return match[0]
        counts.places[label(match.start())] += 1
        return f"{match['root']}{separator}_{match['child']}"

    return pattern.sub(replace, text)


def _sub_paths(text: str, patterns: Patterns, counts: Counts) -> str:
    return _sub(patterns.path, text, patterns, counts, lambda _: FILE_PATH, "/")


def old_references(text: str, patterns: Patterns, hy: bool) -> int:
    """text に残った古い path(module path と file の path)の数。"""
    dotted = patterns.dotted_hy if hy else patterns.dotted
    return sum(
        1
        for pattern in (dotted, patterns.path)
        for match in pattern.finditer(text)
        if patterns.child_of(match["root"], match["child"]) is not None
    )


# --- Python ---


def _python_spans(text: str) -> Spans:
    """comment・文字列・import の文の範囲(読めない所は既定の種類のまま)。"""
    starts = _line_starts(text)
    try:
        tokens = tuple(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        tokens = ()
    words = tuple(
        Span(
            starts[token.start[0] - 1] + token.start[1],
            starts[token.end[0] - 1] + token.end[1],
            COMMENT if token.type == tokenize.COMMENT else STRING,
        )
        for token in tokens
        if token.type == tokenize.COMMENT or "STRING" in tokenize.tok_name[token.type]
    )
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return Spans(words)
    imports = tuple(
        Span(
            _offset(text, starts, node.lineno, node.col_offset),
            _offset(text, starts, node.end_lineno, node.end_col_offset or 0),
            PY_IMPORT,
        )
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node.end_lineno is not None
    )
    return Spans(words + imports)


def _alias_text(alias: ast.alias) -> str:
    return f"{alias.name} as {alias.asname}" if alias.asname else alias.name


def _resolve_relative(module: str, is_package: bool, level: int, target: str | None) -> str | None:
    """相対 import の的の絶対の名(file の module の名から)。"""
    parts = module.split(".") if is_package else module.split(".")[:-1]
    if level - 1 > len(parts) - 1:
        return None
    anchor = parts[: len(parts) - (level - 1)]
    return ".".join([*anchor, *([target] if target else [])])


def _hidden_target(name: str, children: Sequence[Child]) -> Child | None:
    return next((c for c in children if name == c.old or name.startswith(c.old + ".")), None)


def _renamed(name: str, child: Child) -> str:
    return child.new + name[len(child.old) :]


def _statement_comments(text: str, first: int, last: int) -> dict[int, str]:
    """行 first〜last の comment(行の番号から comment の text へ)。"""
    lines = text.splitlines(keepends=True)[first - 1 : last]
    try:
        tokens = tuple(tokenize.generate_tokens(io.StringIO("".join(lines)).readline))
    except (tokenize.TokenError, SyntaxError):
        return {}
    return {first + t.start[0] - 1: t.string for t in tokens if t.type == tokenize.COMMENT}


def _render_from(indent: str, module: str, items: list[tuple[str, str | None]], head: str) -> str:
    """``from module import …`` の文(長いか名に comment が在れば括弧で行を分ける)。"""
    names = [text for text, _ in items]
    tail = f"  {head}" if head else ""
    one = f"{indent}from {module} import {', '.join(names)}"
    if all(comment is None for _, comment in items) and len(one) + len(tail) <= LINE_LIMIT:
        return one + tail
    body = "".join(
        f"{indent}    {text},{'  ' + comment if comment else ''}\n" for text, comment in items
    )
    return f"{indent}from {module} import ({tail}\n{body}{indent})"


@dataclass
class _Edit:
    start: int
    end: int
    text: str


def _apply(text: str, edits: list[_Edit]) -> str:
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.text + text[edit.end :]
    return text


class _PythonImports:
    """Python の text の ``from`` の文を直す: 相対 import・``from <根> import <子>``・入口へ分ける。"""

    def __init__(
        self, text: str, patterns: Patterns, context: FileContext, decide: Decide, counts: Counts
    ):
        self.text = text
        self.patterns = patterns
        self.context = context
        self.decide = decide
        self.counts = counts
        self.starts = _line_starts(text)
        self.roots = {c.root for c in patterns.children}

    def run(self, tree: ast.Module) -> str:
        edits = [
            edit
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for edit in self._edits(node)
        ]
        return _apply(self.text, edits)

    def _span(self, node: ast.stmt) -> tuple[int, int]:
        assert node.end_lineno is not None
        start = _offset(self.text, self.starts, node.lineno, node.col_offset)
        end = _offset(self.text, self.starts, node.end_lineno, node.end_col_offset or 0)
        return start, end

    def _edits(self, node: ast.ImportFrom) -> list[_Edit]:
        if node.level:
            return self._relative(node)
        if node.module in self.roots:
            return self._child_names(node, node.module)
        if node.module is None or self.context.underscore_only:
            return []
        child = next(
            (
                c
                for c in self.patterns.children
                if node.module == c.new or node.module.startswith(c.new + ".")
            ),
            None,
        )
        return [] if child is None else self._split(node, node.module, child)

    def _relative(self, node: ast.ImportFrom) -> list[_Edit]:
        module = self.context.module
        if module is None:
            return []
        is_package = Path(self.context.relative).stem == "__init__"
        target = _resolve_relative(module, is_package, node.level, node.module)
        if target is None:
            return []
        if target in self.roots:
            return self._child_names(node, target)
        child = _hidden_target(target, self.patterns.children)
        if child is None or node.module is None:
            return []
        # 的の名の子の部分(<根>.<子> の 2 つ目)が、相対の名の何番目か。
        relative = node.module.split(".")
        index = 1 - (len(target.split(".")) - len(relative))
        if index < 0:
            return []
        relative[index] = f"_{child.name}"
        start, end = self._span(node)
        segment = self.text[start:end]
        replaced = re.sub(
            r"^(from\s+\.+)" + re.escape(node.module),
            lambda m: m[1] + ".".join(relative),
            segment,
            count=1,
        )
        self.counts.places[PY_IMPORT] += 1
        return [_Edit(start, end, replaced)]

    def _child_names(self, node: ast.ImportFrom, root: str) -> list[_Edit]:
        """``from <根> import <子>`` の子を ``_<子> as <子>`` に。"""
        hidden = self.patterns.hidden_names(root)
        names = [alias for alias in node.names if alias.name in hidden]
        if not names:
            return []
        start, end = self._span(node)
        segment = self.text[start:end]
        head, keyword, rest = segment.partition(" import ")
        for alias in names:
            rest = re.sub(
                rf"(?<![\w.]){re.escape(alias.name)}(?![\w])(\s+as\s+\w+)?",
                lambda m, n=alias.name: f"_{n}{m[1]}" if m[1] else f"_{n} as {n}",
                rest,
                count=1,
            )
            self.counts.places[PY_IMPORT] += 1
        return [_Edit(start, end, head + keyword + rest)]

    def _split(self, node: ast.ImportFrom, module: str, child: Child) -> list[_Edit]:
        """呼び手の ``from <根>._<子>… import`` を、入口の名の文と ``_`` の path の文に分ける。"""
        assert node.end_lineno is not None
        lines = self.text.splitlines(keepends=True)
        first_line = lines[node.lineno - 1]
        start, end = self._span(node)
        line_start = self.starts[node.lineno - 1]
        line_end = self.starts[node.end_lineno - 1] + len(lines[node.end_lineno - 1].rstrip("\r\n"))
        after = self.text[end:line_end].strip()
        # 1 行に別の文と並ぶ import は分けない(_ の path のまま — 意味は同じ)。
        alone = not self.text[line_start:start].strip() and (not after or after.startswith("#"))
        flags = [
            alone
            and alias.name != "*"
            and not alias.name.startswith("_")
            and self.decide(module, alias.name)
            for alias in node.names
        ]
        self.counts.to_entry.update(
            (module, a.name) for a, f in zip(node.names, flags, strict=True) if f
        )
        self.counts.to_underscore.update(
            (module, a.name)
            for a, f in zip(node.names, flags, strict=True)
            if not f and a.name != "*"
        )
        if not any(flags):
            return []
        comments = _statement_comments(self.text, node.lineno, node.end_lineno)
        multiline = node.lineno != node.end_lineno
        attached = {
            index: comments[alias.lineno]
            for index, alias in enumerate(node.names)
            if multiline
            and alias.lineno in comments
            and all(other.lineno != alias.lineno for other in node.names[index + 1 :])
        }
        head = "  ".join(t for _line, t in sorted(comments.items()) if t not in attached.values())
        indent = first_line[: len(first_line) - len(first_line.lstrip())]
        items = [(_alias_text(a), attached.get(i)) for i, a in enumerate(node.names)]
        entry = [item for item, f in zip(items, flags, strict=True) if f]
        rest = [item for item, f in zip(items, flags, strict=True) if not f]
        statements = [
            _render_from(indent, child.root, entry, head),
            *([_render_from(indent, module, rest, head)] if rest else []),
        ]
        return [_Edit(line_start, line_end, "\n".join(statements))]


def rewrite_python(
    text: str, patterns: Patterns, context: FileContext, decide: Decide
) -> tuple[str, Counts]:
    """Python の text を書き換える。構文を読めない text は module path と file の path だけを書き換える。"""
    counts = Counts()
    spans = _python_spans(text)
    text = _sub(patterns.dotted, text, patterns, counts, lambda p: spans.label_at(p, CODE), ".")
    text = _sub_paths(text, patterns, counts)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        counts.unread += 1
        return text, counts
    return _PythonImports(text, patterns, context, decide, counts).run(tree), counts


# --- Hy ---

_HY_TOKEN = re.compile(
    r"""
    (?P<comment>;[^\n]*)
  | (?P<bstring>\#\[(?P<delim>[^\[\]\s]*)\[.*?\](?P=delim)\])
  | (?P<string>[a-zA-Z]{0,2}"(?:[^"\\]|\\.)*")
  | (?P<open>\#?[(\[{])
  | (?P<close>[)\]}])
  | (?P<atom>[^\s()\[\]{}";]+)
  | (?P<space>\s+)
    """,
    re.VERBOSE | re.DOTALL,
)


@dataclass(frozen=True)
class HyNode:
    """Hy の text の形 1 つ(字の範囲つき): 括弧の形・atom・文字列。"""

    kind: Literal["form", "atom", "string"]
    start: int
    end: int
    opener: str = ""
    text: str = ""
    children: tuple[HyNode, ...] = ()


@dataclass(frozen=True)
class HyText:
    """Hy の text を読んだ物: 形の木と comment の範囲。"""

    nodes: tuple[HyNode, ...]
    comments: tuple[Span, ...]


class HyUnreadableError(Exception):
    """Hy の字句として読めない text(閉じない括弧・開かない閉じ括弧・字句の外の字)。"""


def _hy_tokens(text: str) -> Iterator[re.Match[str]]:
    position = 0
    while position < len(text):
        match = _HY_TOKEN.match(text, position)
        if match is None:
            raise HyUnreadableError(f"{position} 字目を字句に読めない")
        position = match.end()
        if match.lastgroup != "space":
            yield match


def _hy_forms(tokens: Sequence[re.Match[str]], index: int) -> tuple[tuple[HyNode, ...], int]:
    """index から閉じ括弧(か終わり)までの形を読む。(形の列, 止まった index) を返す。"""
    nodes: tuple[HyNode, ...] = ()
    while index < len(tokens) and tokens[index].lastgroup != "close":
        token = tokens[index]
        if token.lastgroup == "open":
            children, close = _hy_forms(tokens, index + 1)
            if close >= len(tokens):
                raise HyUnreadableError(f"{token.start()} 字目の括弧が閉じない")
            node = HyNode("form", token.start(), tokens[close].end(), token[0], children=children)
            index = close + 1
        else:
            kind: Literal["atom", "string"] = "atom" if token.lastgroup == "atom" else "string"
            node = HyNode(kind, token.start(), token.end(), text=token[0])
            index += 1
        nodes = (*nodes, node)
    return nodes, index


def parse_hy(text: str) -> HyText:
    """Hy の text を形の木と comment の範囲に読む(読めなければ HyUnreadableError)。"""
    tokens = tuple(_hy_tokens(text))
    code = tuple(token for token in tokens if token.lastgroup != "comment")
    nodes, index = _hy_forms(code, 0)
    if index != len(code):
        raise HyUnreadableError(f"{code[index].start()} 字目の閉じ括弧が開いていない")
    comments = tuple(Span(t.start(), t.end(), COMMENT) for t in tokens if t.lastgroup == "comment")
    return HyText(nodes, comments)


def _walk_hy(nodes: Iterable[HyNode]) -> Iterator[HyNode]:
    for node in nodes:
        yield node
        yield from _walk_hy(node.children)


def _hy_head(node: HyNode) -> str | None:
    """``(head …)`` の head の atom(括弧の形でなければ None)。"""
    if (
        node.kind != "form"
        or node.opener != "("
        or not node.children
        or node.children[0].kind != "atom"
    ):
        return None
    return node.children[0].text


def _hy_mangle(text: str) -> str:
    """Hy で書いた名を Python の名へ(``a-b.c?`` -> ``a_b.hyx_cXquestion_markX``)。

    相対の名(``.runtime-env-model``)の頭の ``.`` はそのまま残す(``hy.mangle`` は空の名を受けない)。
    """
    hy = importlib.import_module("hy")
    return ".".join(str(hy.mangle(part)) if part else part for part in text.split("."))


def _hy_spans(parsed: HyText) -> Spans:
    labels = {"import": HY_IMPORT, "require": HY_REQUIRE}
    nodes = tuple(_walk_hy(parsed.nodes))
    strings = tuple(Span(n.start, n.end, STRING) for n in nodes if n.kind == "string")
    forms = tuple(
        Span(n.start, n.end, labels[head]) for n in nodes if (head := _hy_head(n)) in labels
    )
    return Spans(parsed.comments + strings + forms)


def _hy_pairs(form: HyNode) -> Iterator[tuple[HyNode, HyNode]]:
    """``(import m1 [names] m2 :as x m3 …)`` の (module, 名の list) の組。"""
    items = form.children[1:]
    index = 0
    while index < len(items):
        module = items[index]
        following = items[index + 1] if index + 1 < len(items) else None
        if (
            module.kind == "atom"
            and following is not None
            and following.kind == "form"
            and following.opener == "["
        ):
            yield module, following
            index += 2
        elif following is not None and following.kind == "atom" and following.text == ":as":
            index += 3
        else:
            index += 1


def _hy_names(names: HyNode) -> Iterator[tuple[HyNode, HyNode | None]]:
    """名の list の (名, 別名) の組(``[a b :as c]``)。"""
    items = names.children
    index = 0
    while index < len(items):
        aliased = (
            index + 2 < len(items)
            and items[index + 1].kind == "atom"
            and items[index + 1].text == ":as"
        )
        yield items[index], (items[index + 2] if aliased else None)
        index += 3 if aliased else 1


def _column(text: str, position: int) -> int:
    return position - (text.rfind("\n", 0, position) + 1)


def _hy_item(name: HyNode, alias: HyNode | None) -> str:
    return name.text if alias is None else f"{name.text} :as {alias.text}"


class _HyImports:
    """Hy の ``(import …)`` を直す: ``(import <根> [<子>])`` と、呼び手の ``_`` の path の名を入口へ分ける。"""

    def __init__(
        self, text: str, patterns: Patterns, context: FileContext, decide: Decide, counts: Counts
    ):
        self.text = text
        self.patterns = patterns
        self.context = context
        self.decide = decide
        self.counts = counts

    def run(self, parsed: HyText) -> str:
        edits = [
            edit
            for form in _walk_hy(parsed.nodes)
            if _hy_head(form) == "import"
            for module, names in _hy_pairs(form)
            for edit in self._pair(module, names)
        ]
        return _apply(self.text, edits)

    def _pair(self, module: HyNode, names: HyNode) -> list[_Edit]:
        mangled = _hy_mangle(module.text)
        if mangled in {c.root for c in self.patterns.children}:
            return self._child_names(mangled, names)
        child = next(
            (
                c
                for c in self.patterns.children
                if mangled == c.new or mangled.startswith(c.new + ".")
            ),
            None,
        )
        if child is None or self.context.underscore_only:
            return []
        pairs = list(_hy_names(names))
        plain = [_hy_mangle(name.text) for name, _alias in pairs]
        flags = [
            name.kind == "atom"
            and name.text != "*"
            and not python_name.startswith("_")
            and self.decide(mangled, python_name)
            for (name, _alias), python_name in zip(pairs, plain, strict=True)
        ]
        self.counts.to_entry.update((mangled, p) for p, f in zip(plain, flags, strict=True) if f)
        self.counts.to_underscore.update(
            (mangled, p) for p, f in zip(plain, flags, strict=True) if not f
        )
        if not any(flags):
            return []
        entry = " ".join(_hy_item(n, a) for (n, a), f in zip(pairs, flags, strict=True) if f)
        rest = " ".join(_hy_item(n, a) for (n, a), f in zip(pairs, flags, strict=True) if not f)
        indent = " " * _column(self.text, module.start)
        replaced = f"{module.text.split('.')[0]} [{entry}]" + (
            f"\n{indent}{module.text} [{rest}]" if rest else ""
        )
        return [_Edit(module.start, names.end, replaced)]

    def _child_names(self, root: str, names: HyNode) -> list[_Edit]:
        hidden = self.patterns.hidden_names(root)
        edits = [
            _Edit(
                name.start, name.end, f"_{name.text}" if alias else f"_{name.text} :as {name.text}"
            )
            for name, alias in _hy_names(names)
            if name.kind == "atom" and _hy_mangle(name.text) in hidden
        ]
        self.counts.places[HY_IMPORT] += len(edits)
        return edits


def rewrite_hy(
    text: str, patterns: Patterns, context: FileContext, decide: Decide
) -> tuple[str, Counts]:
    """Hy の text を書き換える。字句を読めない text は module path と file の path だけを書き換える。"""
    counts = Counts()
    try:
        spans = _hy_spans(parse_hy(text))
    except HyUnreadableError:
        spans = Spans()
        counts.unread += 1
    text = _sub(patterns.dotted_hy, text, patterns, counts, lambda p: spans.label_at(p, CODE), ".")
    text = _sub_paths(text, patterns, counts)
    try:
        parsed = parse_hy(text)
    except HyUnreadableError:
        return text, counts
    return _HyImports(text, patterns, context, decide, counts).run(parsed), counts


# --- markdown と設定 ---

_FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]{0,3})(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^\n]*)$")
_INLINE_CODE = re.compile(r"(?P<ticks>`+)(?P<code>.+?)(?P=ticks)")


def rewrite_config(text: str, patterns: Patterns) -> tuple[str, Counts]:
    counts = Counts()
    text = _sub(patterns.dotted, text, patterns, counts, lambda _: CONFIG, ".")
    return _sub_paths(text, patterns, counts), counts


def rewrite_markdown(
    text: str, patterns: Patterns, context: FileContext, decide: Decide
) -> tuple[str, Counts]:
    """code の囲み(言語ごと)と行の中の code を書き換える。地の文は書き換えない(古い path として残る)。"""
    counts = Counts()
    lines = text.splitlines(keepends=True)
    pieces = (
        _markdown_piece(piece, patterns, context, decide, counts)
        for piece in _markdown_pieces(lines)
    )
    return "".join(pieces), counts


@dataclass(frozen=True)
class _MarkdownPiece:
    """markdown の 1 片: 地の文の 1 行か、code の囲み 1 つ(開く行・中身・閉じる行と言語)。"""

    line: str = ""
    opening: str = ""
    body: str = ""
    closing: str = ""
    language: str | None = None


def _markdown_pieces(lines: Sequence[str]) -> Iterator[_MarkdownPiece]:
    """markdown の行を、地の文の行と code の囲みに分ける。"""
    index = 0
    while index < len(lines):
        opened = _FENCE_OPEN.match(lines[index].rstrip("\r\n"))
        if opened is None:
            yield _MarkdownPiece(line=lines[index])
            index += 1
            continue
        fence = opened["fence"]
        closing = re.compile(rf"^[ \t]{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
        end = next(
            (i for i in range(index + 1, len(lines)) if closing.match(lines[i].rstrip("\r\n"))),
            len(lines),
        )
        yield _MarkdownPiece(
            opening=lines[index],
            body="".join(lines[index + 1 : end]),
            closing="".join(lines[end : end + 1]),
            language=(opened["info"].split() or [""])[0].lower(),
        )
        index = end + 1


def _markdown_piece(
    piece: _MarkdownPiece, patterns: Patterns, context: FileContext, decide: Decide, counts: Counts
) -> str:
    """1 片を書き換える: 地の文は行の中の code だけ、囲みは言語ごとの書き換え。"""
    if piece.language is None:
        return _INLINE_CODE.sub(lambda m: _inline(m, patterns, counts), piece.line)
    if piece.language in _PYTHON_FENCES:
        body, found = rewrite_python(piece.body, patterns, context, decide)
    elif piece.language in _HY_FENCES:
        body, found = rewrite_hy(piece.body, patterns, context, decide)
    else:
        body, found = rewrite_config(piece.body, patterns)
    counts.merge(found, relabel=MARKDOWN)
    return piece.opening + body + piece.closing


def _inline(match: re.Match[str], patterns: Patterns, counts: Counts) -> str:
    code, found = rewrite_config(match["code"], patterns)
    counts.merge(found, relabel=MARKDOWN)
    return f"{match['ticks']}{code}{match['ticks']}"


FileKind = Literal["python", "hy", "markdown", "config"]


def file_kind(relative: str) -> FileKind | None:
    path = Path(relative)
    if path.suffix in PYTHON_SUFFIXES:
        return "python"
    if path.suffix in HY_SUFFIXES:
        return "hy"
    if path.suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    if path.suffix in CONFIG_SUFFIXES or path.name in CONFIG_NAMES:
        return "config"
    return None


def rewrite_text(
    kind: FileKind, text: str, patterns: Patterns, context: FileContext, decide: Decide
) -> tuple[str, Counts]:
    if kind == "python":
        return rewrite_python(text, patterns, context, decide)
    if kind == "hy":
        return rewrite_hy(text, patterns, context, decide)
    if kind == "markdown":
        return rewrite_markdown(text, patterns, context, decide)
    return rewrite_config(text, patterns)


# --- 木 ---


def _git(tree: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *arguments], capture_output=True, text=True, check=True
    ).stdout


def git_toplevel(path: Path) -> Path:
    return Path(
        _git(path if path.is_dir() else path.parent, "rev-parse", "--show-toplevel").strip()
    )


def history_prefixes(tree: Path) -> tuple[str, ...]:
    """歴史として書き換えない dir(木の ``[tool.doeff] record-dirs`` と既定)。"""
    pyproject = tree / "pyproject.toml"
    extra: tuple[str, ...] = ()
    if pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        tool = data.get("tool", {})
        doeff = tool.get("doeff", {}) if isinstance(tool, dict) else {}
        dirs = doeff.get("record-dirs", []) if isinstance(doeff, dict) else []
        extra = tuple(str(entry) for entry in dirs) if isinstance(dirs, list) else ()
    return tuple(sorted({*HISTORY_PREFIXES, *extra}))


def is_history(relative: str, prefixes: Sequence[str]) -> bool:
    path = Path(relative)
    return (
        relative.startswith(tuple(prefixes))
        or any(part in HISTORY_DIR_NAMES for part in path.parts[:-1])
        or path.name in HISTORY_FILE_NAMES
    )


@dataclass(frozen=True)
class Tree:
    """書き換える木: 根・``_`` の path だけを読む所・書き換えない file。"""

    path: Path
    underscore_only: tuple[str, ...]
    kept: frozenset[str]
    roots: dict[str, str]  # import の根の dir(木からの相対)から根の名へ

    def context(self, relative: str) -> FileContext:
        """file 1 つの書き換えの前提: import の根の中なら module の名、``_`` の path だけを読むか。"""
        module = next(
            (
                ".".join(
                    [
                        root,
                        *Path(relative[len(directory) + 1 :]).parent.parts,
                        *([] if Path(relative).stem == "__init__" else [Path(relative).stem]),
                    ]
                )
                for directory, root in self.roots.items()
                if relative.startswith(directory + "/")
            ),
            None,
        )
        inside = module is not None or any(
            relative == entry or relative.startswith(entry.rstrip("/") + "/")
            for entry in self.underscore_only
        )
        return FileContext(relative, module, inside)

    def candidates(self, patterns: Patterns) -> list[tuple[str, FileKind]]:
        """古い path を読みうる file(根の名を含む file と、import の根の下の file)。"""
        prefixes = history_prefixes(self.path)
        tokens = [argument for token in patterns.tokens() for argument in ("-e", token)]
        found = subprocess.run(
            ["git", "-C", str(self.path), "grep", "-l", "-z", "-I", "-F", *tokens],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split("\0")
        inner = (
            _git(self.path, "ls-files", "-z", "--", *self.roots).split("\0") if self.roots else []
        )
        return [
            (relative, kind)
            for relative in sorted({*found, *inner} - {""})
            if (kind := file_kind(relative)) is not None
            and relative not in self.kept
            and not is_history(relative, prefixes)
            and (self.path / relative).is_file()
        ]


@dataclass(frozen=True)
class FileResult:
    """file 1 つの書き換えの結末。"""

    relative: str
    original: str
    text: str
    counts: Counts
    before: int
    left: int


@dataclass(frozen=True)
class TreeResult:
    """木の書き換えの結末: 数・変えた file・古い path(前)・残った古い path(file ごと)・読めなかった file。"""

    counts: Counts
    changed: tuple[str, ...]
    before: int
    remaining: dict[str, int]
    unread: tuple[str, ...]


def rewrite_tree(
    tree: Tree,
    patterns: Patterns,
    same_as_entry: Callable[[set[tuple[str, str]]], set[tuple[str, str]]],
    *,
    write: bool,
) -> TreeResult:
    """木の候補の file を 2 回読む: 1 回目で入口と比べる名を集め、子の process で確かめてから 2 回目で書く。

    ``_`` の path だけを読む file(import の根の中と ``--underscore-only``)は入口と比べずに決まるので、書く時は子の
    process より前に書く。根の中に古い path が残ると、子の process が根を読む時に、子の package の中から子を古い path で
    読む import(``_<子>/__init__`` の ``from <根>.<子>.<下> import …``)が、まだ package でない移し替えの module に
    当たって落ち、どの名も入口と同じと確かめられずに ``_`` の path へ向く。
    """
    files = tree.candidates(patterns)
    texts = {relative: (tree.path / relative).read_text(encoding="utf-8") for relative, _ in files}
    asked: set[tuple[str, str]] = set()

    def collect(module: str, name: str) -> bool:
        # 1 回目: 呼び手が _ の path から読む名を集めるだけ(どれも _ の path のまま)。
        asked.add((module, name))
        return False

    for relative, kind in files:
        context = tree.context(relative)
        text, _counts = rewrite_text(kind, texts[relative], patterns, context, collect)
        if write and context.underscore_only and text != texts[relative]:
            (tree.path / relative).write_text(text, encoding="utf-8")
    entry = same_as_entry(asked)

    def one(relative: str, kind: FileKind) -> FileResult:
        original = texts[relative]
        text, counts = rewrite_text(
            kind, original, patterns, tree.context(relative), lambda m, n: (m, n) in entry
        )
        flexible = kind in ("hy", "markdown")
        return FileResult(
            relative,
            original,
            text,
            counts,
            old_references(original, patterns, flexible),
            old_references(text, patterns, flexible),
        )

    results = [one(relative, kind) for relative, kind in files]
    changed = tuple(r.relative for r in results if r.text != r.original)
    if write:
        for result in results:
            if result.text != result.original:
                (tree.path / result.relative).write_text(result.text, encoding="utf-8")
    total = Counts()
    for result in results:
        total.merge(result.counts)
    return TreeResult(
        counts=total,
        changed=changed,
        before=sum(r.before for r in results),
        remaining={r.relative: r.left for r in results if r.left},
        unread=tuple(r.relative for r in results if r.counts.unread),
    )


# --- 入口と比べる(子の process) ---


def _root_paths(layout: _Layout) -> list[str]:
    """package を checkout の source から読む為に sys.path の先頭へ置く dir(maturin は組み立てた物を読む)。"""
    return [] if layout.maturin else sorted({str(root.path.parent) for root in layout.roots})


class _IdentityPayload(TypedDict):
    paths: list[str]
    pairs: list[list[str]]


def same_as_entry(
    layout: _Layout, children: Sequence[Child]
) -> Callable[[set[tuple[str, str]]], set[tuple[str, str]]]:
    """(module, 名) のうち、根の入口が同じ物を出す組を、子の process で確かめる関数。"""
    root_of = {c.new: c.root for c in children}

    def check(pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
        if not pairs:
            return set()
        ordered = sorted(pairs)
        payload: _IdentityPayload = {
            "paths": _root_paths(layout),
            "pairs": [
                [
                    module,
                    name,
                    next(
                        root
                        for new, root in root_of.items()
                        if module == new or module.startswith(new + ".")
                    ),
                ]
                for module, name in ordered
            ],
        }
        with tempfile.TemporaryDirectory(prefix="hide-submodules-") as scratch:
            given = Path(scratch) / "pairs.json"
            answer = Path(scratch) / "same.json"
            given.write_text(json.dumps(payload), encoding="utf-8")
            finished = subprocess.run(
                [
                    sys.executable,
                    "-P",
                    str(Path(__file__).resolve()),
                    "--identity",
                    str(given),
                    "--identity-out",
                    str(answer),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if finished.returncode != 0 or not answer.is_file():
                tail = " / ".join(finished.stderr.strip().splitlines()[-5:])
                raise SystemExit(
                    f"入口と比べる子の process が終了 code {finished.returncode} で終わった: {tail}"
                )
            same = cast(list[bool], json.loads(answer.read_text(encoding="utf-8")))
        return {pair for pair, flag in zip(ordered, same, strict=True) if flag}

    return check


def _root_reexports(root: types.ModuleType) -> set[str]:
    """根の ``__init__.py`` が ``X as X`` で出す名。"""
    file = root.__dict__.get("__file__")
    if not isinstance(file, str) or not file.endswith(".py"):
        return set()
    tree = ast.parse(Path(file).read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname == alias.name
    }


def _shown_by_root(root: types.ModuleType, name: str, value: object) -> bool:
    """根が名を公開の名として出すか(遅延の表・``X as X``・``__all__``・package の中で定義した物)。

    ``__all__`` は入口が名を出す宣言なので、``@handler``・``@do`` で包んで定義の module が包みの側になった物も入口の名と
    読む(card ki-32a05dc657b8 — doeff-gemini の ``default_gemini_cost_handler`` の定義の module は ``doeff.program``)。
    """
    lazy = root.__dict__.get("_LAZY_EXPORTS")
    if isinstance(lazy, dict) and name in lazy:
        return True
    if name in _root_reexports(root):
        return True
    shown = root.__dict__.get("__all__")
    if isinstance(shown, (list, tuple)) and name in shown:
        return True
    owner = inspect.getmodule(value)
    return (
        name in root.__dict__
        and owner is not None
        and owner.__name__.split(".")[0] == root.__name__
    )


def identity_probe(payload: _IdentityPayload) -> list[bool]:
    """子の process: (module, 名, 根) ごとに、根の入口の名が module の名と同じ物か。"""
    sys.path[:0] = payload["paths"]
    if importlib.util.find_spec("hy") is not None:
        importlib.import_module("hy")
    return [_same_in_root(module, name, root) for module, name, root in payload["pairs"]]


def _same_in_root(module_name: str, name: str, root_name: str) -> bool:
    """呼び手を入口へ向けてよいか: 根が名を公開の名として出し、それが module の名と同じ物である事。

    import できない・名の無い組は、入口へ向けず ``_`` の path のまま読ませる(呼び手の意味は変わらない)。
    数には「_ の path へ向けた名」として出る。
    """
    try:
        module = importlib.import_module(module_name)
        root = importlib.import_module(root_name)
        value = getattr(module, name)
        return (
            not isinstance(value, types.ModuleType)
            and _shown_by_root(root, name, value)
            and getattr(root, name) is value
        )
    except Exception:  # 確かめられない組は _ の path のまま(上の docstring)
        return False


# --- 名の行き先(hide) ---


class ExportRow(TypedDict):
    module: str
    name: str
    kind: str
    form: str
    defined_at: str
    reason: str


@dataclass(frozen=True)
class NameFate:
    """子の名 1 つの行き先。"""

    name: str
    modules: tuple[str, ...]
    kind: str
    group: str | None
    fate: Fate
    source: str | None = None  # 入口が読む module(新しい path)
    note: str = ""
    definition: str = ""  # 入口へ出す名の定義の場所(目録の defined_at)


def _table_groups(
    table: dict[str, object] | None, package_dir: str
) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
    if table is None:
        return {}, set()
    packages = cast(dict[str, dict[str, object]], table["packages"])
    entry = packages.get(package_dir, {})
    groups = cast(dict[str, dict[str, list[str]]], entry.get("名", {}))
    named = {
        (module, name): group
        for group, modules in groups.items()
        for module, names in modules.items()
        for name in names
    }
    added = {
        (row["module"], row["name"])
        for row in cast(list[dict[str, str]], table.get("足した名", []))
        if row["package"] == package_dir
    }
    return named, added


def _row_destination(
    row: ExportRow, groups: dict[tuple[str, str], str], added: set[tuple[str, str]]
) -> tuple[Fate, str | None, str]:
    if row["form"] == "Hy の macro":
        return "macro", None, ""
    group = groups.get((row["module"], row["name"]))
    if row["kind"] in ("effect", "handler"):
        return "entry", group, ""
    if group is not None:
        return ("underscore" if group.startswith("外れ") else "entry"), group, ""
    if (row["module"], row["name"]) in added:
        return "entry", "足した名", ""
    return "underscore", None, "既知の表に無い effect と handler の外の名"


def _preferred_module(rows: Sequence[ExportRow]) -> str:
    """同じ定義を出す module のうち、定義の在る module(無ければ浅い module)。"""

    def key(row: ExportRow) -> tuple[bool, int, str]:
        defined = row["defined_at"].split(":")[0]
        here = defined == row["module"] or defined.endswith(
            row["module"].replace(".", "/") + Path(defined).suffix
        )
        return (not here, row["module"].count("."), row["module"])

    return min(rows, key=key)["module"]


def decide_names(
    rows: Sequence[ExportRow],
    children: Sequence[Child],
    groups: dict[tuple[str, str], str],
    added: set[tuple[str, str]],
) -> list[NameFate]:
    """子とその下の module が出す名を、入口へ出す・既に在る・``_`` に残す・macro・ぶつかりに分ける。"""
    fates = [
        _name_fate(child, name, named, rows, groups, added)
        for child in children
        for name, named in _rows_by_name(rows, child)
    ]
    return _dedupe_across_children(fates)


def _rows_by_name(rows: Sequence[ExportRow], child: Child) -> list[tuple[str, list[ExportRow]]]:
    """子とその下の module の行を、名ごとに集める(名の順)。"""
    mine = [r for r in rows if _hidden_target(r["module"], [child]) is not None]
    return [
        (name, [r for r in mine if r["name"] == name]) for name in sorted({r["name"] for r in mine})
    ]


def _name_fate(
    child: Child,
    name: str,
    named: Sequence[ExportRow],
    rows: Sequence[ExportRow],
    groups: dict[tuple[str, str], str],
    added: set[tuple[str, str]],
) -> NameFate:
    """子の名 1 つの行き先: 入口へ出す名が無ければ表の分けのまま、在れば定義を入口の名と比べる。"""
    destinations = [(row, *_row_destination(row, groups, added)) for row in named]
    entry_rows = [row for row, fate, _g, _n in destinations if fate == "entry"]
    modules = tuple(sorted(row["module"] for row in named))
    kind = named[0]["kind"]
    group = next((g for _r, _f, g, _n in destinations if g), None)
    if not entry_rows:
        return NameFate(name, modules, kind, group, destinations[0][1], note=destinations[0][3])
    definitions = {row["defined_at"] for row in entry_rows}
    if len(definitions) > 1:
        note = f"定義が {len(definitions)} つ: {', '.join(sorted(definitions))}"
        return NameFate(name, modules, kind, group, "conflict", note=note)
    existing = {r["defined_at"] for r in rows if r["module"] == child.root and r["name"] == name}
    if existing:
        if existing == definitions:
            return NameFate(name, modules, kind, group, "already")
        note = f"入口の {name} は別の定義({', '.join(sorted(existing))})"
        return NameFate(name, modules, kind, group, "conflict", note=note)
    source = _renamed(_preferred_module(entry_rows), child)
    [definition] = definitions
    return NameFate(name, modules, kind, group, "entry", source=source, definition=definition)


def _defines(fate: NameFate) -> bool:
    """名の定義が、その子の module(子が package なら下の module)の中に在るか。"""
    defined = fate.definition.split(":")[0]
    suffix = Path(defined).suffix
    return any(
        defined.endswith(
            (module.replace(".", "/") + suffix, module.replace(".", "/") + "/__init__" + suffix)
        )
        for module in fate.modules
    )


def _dedupe_across_children(fates: list[NameFate]) -> list[NameFate]:
    """2 つ以上の子が同じ名を入口へ出す時の行き先。

    定義が 1 つなら(子が別の子の名を import して出し直す)、定義の在る子から 1 度だけ足し、ほかの子の行は「入口に既に
    在る」にする。定義が違えば、どれも足さずにぶつかりにする。
    """
    entries = [f for f in fates if f.fate == "entry"]
    same_name = {name: [f for f in entries if f.name == name] for name in {f.name for f in entries}}
    kept = {
        name: min(group, key=lambda f: (not _defines(f), cast(str, f.source)))
        for name, group in same_name.items()
        if len({f.definition for f in group}) == 1
    }

    def settle(fate: NameFate) -> NameFate:
        """入口に同じ名を 2 度足さず、出し直しの名を落とさない為: 名を出す子の 1 つだけを入口へ向ける。"""
        if fate.fate != "entry" or len(same_name[fate.name]) == 1 or kept.get(fate.name) is fate:
            return fate
        keeper = kept.get(fate.name)
        if keeper is None:
            definitions = sorted({f.definition for f in same_name[fate.name]})
            note = f"2 つ以上の子が違う定義の同じ名を入口へ出す: {', '.join(definitions)}"
            return dataclasses.replace(fate, fate="conflict", source=None, note=note)
        note = f"同じ定義を {keeper.source} から入口へ出す"
        return dataclasses.replace(fate, fate="already", source=None, note=note)

    return [settle(fate) for fate in fates]


# --- 入口と移し替えの module ---


def _module_file(root_dir: Path, root: str, module: str) -> list[Path]:
    """module の source の file(``.py``・``.pyi``・``.hy`` と、package の ``__init__``)。"""
    relative = Path(*module.split(".")[1:]) if module != root else Path()
    base = root_dir / relative
    candidates = [base.with_suffix(s) for s in (".py", ".pyi", ".hy")] if module != root else []
    candidates += [base / f"__init__{s}" for s in (".py", ".pyi", ".hy")]
    return [path for path in candidates if path.is_file()]


def _typed(root_dir: Path, root: str, module: str) -> bool:
    return any(path.suffix in (".py", ".pyi") for path in _module_file(root_dir, root, module))


_GETATTR_TEXT = """

def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
"""

_DIR_TEXT = """

def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
"""

_LAZY_COMMENT = (
    "# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import\n"
    "# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`\n"
    "# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).\n"
)


def _type_checking_line(module: str, name: str, indent: str) -> str:
    line = f"{indent}from {module} import {name} as {name}"
    if len(line) <= LINE_LIMIT:
        return line + "\n"
    return f"{indent}from {module} import (\n{indent}    {name} as {name},\n{indent})\n"


@dataclass(frozen=True)
class EntryShape:
    """根の ``__init__.py`` の、遅延の名に関わる形。"""

    path: Path
    tree: ast.Module | None
    lazy: ast.Dict | None
    lazy_statement: ast.stmt | None
    has_getattr: bool
    has_dir: bool
    type_checking: ast.If | None
    bound: frozenset[str]
    listed: ast.List | ast.Tuple | None = None  # ``__all__`` の値(在れば)

    @classmethod
    def read(cls, path: Path) -> EntryShape:
        if not path.is_file():
            return cls(path, None, None, None, False, False, None, frozenset())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        lazy: ast.Dict | None = None
        lazy_statement: ast.stmt | None = None
        type_checking: ast.If | None = None
        listed: ast.List | ast.Tuple | None = None
        bound: set[str] = set()
        functions: set[str] = set()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in targets
                ) and isinstance(node.value, ast.Dict):
                    lazy, lazy_statement = node.value, node
                if any(
                    isinstance(t, ast.Name) and t.id == "__all__" for t in targets
                ) and isinstance(node.value, (ast.List, ast.Tuple)):
                    listed = node.value
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.add(node.name)
            elif (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                type_checking = node
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        return cls(
            path,
            tree,
            lazy,
            lazy_statement,
            "__getattr__" in functions,
            "__dir__" in functions,
            type_checking,
            frozenset(bound),
            listed,
        )

    def problem(self) -> str | None:
        if self.path.with_suffix(".hy").is_file():
            return f"{self.path.with_suffix('.hy')}: Hy の入口には名を足せない(手で足す)"
        if self.tree is not None and self.has_getattr != (self.lazy is not None):
            return f"{self.path}: __getattr__ と _LAZY_EXPORTS の dict の片方だけが在る — 道具は表に行を足せない(手で足す)"
        if self.listed is not None and not _one_name_per_line(self.listed):
            return (
                f"{self.path}: __all__ が 1 行に 1 つの名の形でない — 道具は名を足せない(手で足す)"
            )
        return None


def _one_name_per_line(listed: ast.List | ast.Tuple) -> bool:
    """``__all__`` が、名の文字列を 1 行に 1 つ並べ、閉じ括弧を行の頭に置く形か(道具が行を足せる形)。"""
    rows = [
        e.lineno for e in listed.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
    ]
    return (
        len(rows) == len(listed.elts)
        and len(set(rows)) == len(rows)
        and listed.lineno not in rows
        and cast(int, listed.end_lineno) > listed.lineno
        and listed.end_col_offset == 1
    )


def _all_order(name: str) -> tuple[int, str]:
    """ruff の RUF022 の並び(定数・class・その他の順で、大文字小文字を分ける)— 名を足した ``__all__`` が lint に
    掛からない為。1 字の大文字(``T``)は class と読む(ruff と同じ)。"""
    if len(name) > 1 and name.isupper():
        return (0, name)
    return (1, name) if name[:1].isupper() else (2, name)


def _listed_rows(shape: EntryShape, additions: Sequence[NameFate]) -> tuple[tuple[int, str], ...]:
    """入口の ``__all__`` に足す名の行: ``__all__`` の在る入口では、目録と ``import *`` が ``__all__`` の名だけを読む為。

    名は RUF022 の並びの位置へ足す(並んでいない ``__all__`` では、後ろに来る名の最初の行の前)。
    """
    listed = shape.listed
    if listed is None or not additions:
        return ()
    # 名の文字列だけが並ぶ事は problem() が _one_name_per_line で確かめた。
    elements = cast(list[ast.Constant], listed.elts)
    indent = " " * (elements[0].col_offset if elements else 4)

    def place(name: str) -> int:
        # 足した後も __all__ が並んだままである為の、名を差し込む行(0 から数える)。
        later = [e.lineno - 1 for e in elements if _all_order(e.value) > _all_order(name)]
        return later[0] if later else cast(int, listed.end_lineno) - 1

    ordered = sorted(additions, key=lambda f: _all_order(f.name))
    return tuple((place(f.name), f'{indent}"{f.name}",\n') for f in ordered)


def _block(
    root: str,
    additions: Sequence[NameFate],
    typed: Sequence[NameFate],
    value: Callable[[NameFate], str],
) -> str:
    """遅延の名の形(注・TYPE_CHECKING の ``X as X``・``_LAZY_EXPORTS``・``__getattr__``・``__dir__``)。"""
    block = _LAZY_COMMENT
    if typed:
        block += "if TYPE_CHECKING:\n"
        block += (
            "".join(_type_checking_line(cast(str, f.source), f.name, "    ") for f in typed) + "\n"
        )
    block += "_LAZY_EXPORTS: dict[str, str] = {\n"
    block += "".join(f'    "{f.name}": "{value(f)}",\n' for f in additions) + "}\n"
    return block + _GETATTR_TEXT + _DIR_TEXT


@dataclass(frozen=True)
class _EntryEdit:
    """入口の text への足し: 行の前に足す text の組・頭に足す import・末尾に足す text。"""

    inserts: tuple[tuple[int, str], ...] = ()
    imports: str = ""
    tail: str = ""


def _merged_edit(
    shape: EntryShape,
    lines: Sequence[str],
    additions: Sequence[NameFate],
    typed: Sequence[NameFate],
    value: Callable[[NameFate], str],
) -> _EntryEdit:
    """``_LAZY_EXPORTS`` と ``__getattr__`` の在る入口へ: 表に行を、TYPE_CHECKING の組に ``X as X`` を足す。"""
    assert shape.lazy is not None
    assert shape.lazy_statement is not None
    closing = cast(int, shape.lazy.end_lineno) - 1
    if not lines[closing].strip().startswith("}"):
        raise SystemExit(
            f"{shape.path}: _LAZY_EXPORTS の閉じ括弧が行の頭に無い — 道具は表に行を足せない(手で足す)"
        )
    rows = (closing, "".join(f'    "{f.name}": "{value(f)}",\n' for f in additions))
    block = "".join(_type_checking_line(cast(str, f.source), f.name, "    ") for f in typed)
    if not typed:
        checking: tuple[tuple[int, str], ...] = ()
    elif shape.type_checking is not None:
        checking = ((cast(int, shape.type_checking.end_lineno), block),)
    else:
        checking = ((shape.lazy_statement.lineno - 1, f"if TYPE_CHECKING:\n{block}\n"),)
    needs_flag = bool(typed) and shape.type_checking is None and "TYPE_CHECKING" not in shape.bound
    return _EntryEdit(
        inserts=(rows, *checking),
        imports="from typing import TYPE_CHECKING\n" if needs_flag else "",
        tail="" if shape.has_dir else _DIR_TEXT,
    )


def _appended_edit(
    shape: EntryShape,
    root: str,
    additions: Sequence[NameFate],
    typed: Sequence[NameFate],
    value: Callable[[NameFate], str],
) -> _EntryEdit:
    """遅延の名の形の無い入口へ: 形を末尾に、要る import を頭に足す。"""
    imports = (
        "" if "import_module" in shape.bound else "from importlib import import_module\n"
    ) + (
        "from typing import TYPE_CHECKING\n" if typed and "TYPE_CHECKING" not in shape.bound else ""
    )
    return _EntryEdit(imports=imports, tail="\n\n" + _block(root, additions, typed, value))


def _relative_values_renamed(
    shape: EntryShape, lines: Sequence[str], children: Sequence[Child]
) -> list[str]:
    """今の表の相対の値(``".<子>…"``)を ``"._<子>…"`` へ(絶対の値は木の書き換えが直す)。"""
    if shape.lazy_statement is None:
        return list(lines)
    first, last = shape.lazy_statement.lineno - 1, cast(int, shape.lazy_statement.end_lineno)
    names = "|".join(re.escape(c.name) for c in children)
    pattern = re.compile(rf"""(["'])\.({names})(?=[."'])""")
    return [
        pattern.sub(r"\1._\2", line) if first <= index < last else line
        for index, line in enumerate(lines)
    ]


def update_entry(
    shape: EntryShape,
    root: str,
    fates: Sequence[NameFate],
    children: Sequence[Child],
    root_dir: Path,
) -> tuple[list[str], list[str]]:
    """入口に名を足す(遅延の表・TYPE_CHECKING の ``X as X``・``__getattr__``・``__dir__``)。

    利用者が submodule でなく入口から名を読める様にする為。足した名と、入口に同じ名の束縛が既に在って足さなかった
    名を返す。入口の無い名前空間 package には入口を作る。
    """
    wanted = [f for f in fates if f.fate == "entry" and f.source is not None]
    keys = shape.lazy.keys if shape.lazy is not None else []
    existing = {key.value for key in keys if isinstance(key, ast.Constant)}
    skipped = [f.name for f in wanted if f.name in existing or f.name in shape.bound]
    additions = [f for f in wanted if f.name not in skipped]
    typed = [f for f in additions if _typed(root_dir, root, cast(str, f.source))]
    if shape.tree is None:
        if additions:
            docstring = f'"""{root}: the effects and handlers of the package (and their types), read on first use."""\n'
            imports = "from importlib import import_module\n" + (
                "from typing import TYPE_CHECKING\n" if typed else ""
            )
            block = _block(root, additions, typed, lambda f: cast(str, f.source))
            shape.path.write_text(f"{docstring}\n{imports}\n{block}", encoding="utf-8")
        return [f.name for f in additions], skipped
    lines = _relative_values_renamed(
        shape, shape.path.read_text(encoding="utf-8").splitlines(keepends=True), children
    )
    values = shape.lazy.values if shape.lazy is not None else []
    relative = bool(values) and all(
        isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.startswith(".")
        for v in values
    )

    def value(fate: NameFate) -> str:
        source = cast(str, fate.source)
        return "." + source[len(root) + 1 :] if relative else source

    if not additions:
        edit = _EntryEdit()
    elif shape.lazy is not None:
        edit = _merged_edit(shape, lines, additions, typed, value)
    else:
        edit = _appended_edit(shape, root, additions, typed, value)
    inserts = (
        *edit.inserts,
        *_listed_rows(shape, additions),
        *(((_import_line(shape), edit.imports),) if edit.imports else ()),
    )
    at = {line: "".join(t for where, t in inserts if where == line) for line, _ in inserts}
    text = "".join(at.get(index, "") + line for index, line in enumerate(lines))
    text += at.get(len(lines), "")
    if edit.tail:
        text = text.rstrip("\n") + "\n" + edit.tail
    shape.path.write_text(text, encoding="utf-8")
    stub = shape.path.with_suffix(".pyi")
    if stub.is_file() and typed:
        with stub.open("a", encoding="utf-8") as handle:
            handle.write(
                "".join(_type_checking_line(cast(str, f.source), f.name, "") for f in typed)
            )
    return [f.name for f in additions], skipped


def _import_line(shape: EntryShape) -> int:
    """足す import を置く行(docstring と ``from __future__`` の後)。"""
    assert shape.tree is not None
    line = 0
    for node in shape.tree.body:
        is_docstring = (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if not (is_docstring or is_future):
            break
        line = cast(int, node.end_lineno)
    return line


_SHIM_FINDER = '''
_OLD = "{old}"
_NEW = "{new}"


class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Answers ``<old>.<sub>`` with the ``<new>.<sub>`` module itself (one module, not a second copy)."""

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(_OLD + ".") or importlib.util.find_spec(self._target(fullname)) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        sys.modules[module.__name__] = import_module(self._target(module.__name__))

    def get_code(self, fullname: str) -> CodeType:
        # python -m <old>.<sub>: run the moved module as __main__.
        target = self._target(fullname)
        source = f"import runpy\\nrunpy.run_module({{target!r}}, run_name='__main__', alter_sys=True)\\n"
        return compile(source, f"<moved {{fullname}}>", "exec")

    @staticmethod
    def _target(fullname: str) -> str:
        return _NEW + fullname[len(_OLD) :]


if not any(isinstance(finder, _MovedSubmodules) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MovedSubmodules())
'''

_SHIM_KEEP = '''

def _keep_entry_name() -> None:
    """Keeps the package's own ``{name}`` when the import system binds this old path on it.

    The package exports a ``{name}`` that is not this module. Before the move the package
    imported the child first and then rebound the name, so ``from {root} import {name}``
    kept giving the package's own object; this keeps it so.
    """
    package = sys.modules["{root}"]

    class _KeepEntryName(type(package)):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "{name}" and isinstance(value, ModuleType):
                return
            super().__setattr__(name, value)

    package.__class__ = _KeepEntryName


_keep_entry_name()
'''


def shim_text(child: Child, typed: bool) -> str:
    """移し替えの module の text: 古い path の import・mock.patch・python -m を、移した module へ渡す為の 1 file。"""
    package = child.is_package()
    keep = child.shadowed_by_entry()
    imports = (
        (
            "import importlib.abc",
            "import importlib.util",
            "import sys",
            "from collections.abc import Sequence",
            "from importlib import import_module",
            "from importlib.machinery import ModuleSpec",
            "from types import CodeType, ModuleType",
        )
        if package
        else (
            "import sys",
            "from importlib import import_module",
            *(("from types import ModuleType",) if keep else ()),
        )
    ) + (("from typing import TYPE_CHECKING",) if typed else ())
    checking = (
        # 型の検だけが古い path で移した module の名を読む為の * (finish が shim ごと消す — 規則の抑えはこの 1 行に閉じる)。
        f"\nif TYPE_CHECKING:\n    from {child.new} import *  # noqa: F403  # nosemgrep: doeff-no-star-imports\n"
        if typed
        else ""
    )
    finder = _SHIM_FINDER.format(old=child.old, new=child.new) if package else ""
    finder += _SHIM_KEEP.format(root=child.root, name=child.name) if keep else ""
    return (
        f"{SHIM_MARK} {child.old} -> {child.new}\n"
        f'"""Moved: ``{child.old}`` is now ``{child.new}`` (ADR-DOE-API-001 R5).\n'
        "\n"
        "Importing the old path gives the moved module itself, so ``mock.patch`` targets and isinstance checks on\n"
        f"the old path act on the same objects, and ``python -m {child.old}…`` runs the moved module. Deleted by\n"
        "``tools/hide_submodules.py finish`` once no repo reads the old path.\n"
        '"""\n'
        "\n" + "\n".join(imports) + "\n" + checking + finder + "\n"
        'if __name__ == "__main__":\n'
        "    import runpy\n"
        "\n"
        f'    runpy.run_module("{child.new}", run_name="__main__", alter_sys=True)\n'
        "else:\n"
        f'    sys.modules[__name__] = import_module("{child.new}")\n'
    )


def is_shim(path: Path, child: Child) -> bool:
    """file が道具の置いた移し替えの module か(1 行目の印で見る — finish が他人の file を消さない為)。"""
    if not path.is_file():
        return False
    first = path.read_text(encoding="utf-8").split("\n", 1)[0]
    return first == f"{SHIM_MARK} {child.old} -> {child.new}"


# --- stage ---


@dataclass(frozen=True)
class RewriteSummary:
    """木の書き換えの数: 古い path を読む場所(前)・書き換えた場所(種類ごと)・入口と ``_`` の path へ向けた名。"""

    places_before: int
    rewritten: dict[str, int]
    files_changed: int
    names_to_entry: int
    names_to_underscore: int
    outliers_to_underscore: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    """1 回の結末(stdout と --json): 前と後の数・入口へ足した名・ぶつかり・注。"""

    stage: str
    package: str
    tree: str
    children: tuple[dict[str, object], ...] = ()
    names: dict[str, list[str]] = field(default_factory=dict)
    rewrite: RewriteSummary | None = None
    remaining: dict[str, int] = field(default_factory=dict)
    entry_added: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    table_rows_removed: dict[str, int] = field(default_factory=dict)
    table_rows_left: dict[str, int] = field(default_factory=dict)

    def as_json(self) -> dict[str, object]:
        """--json に書く形(欄の名のまま)。"""
        return dataclasses.asdict(self)

    def text(self) -> str:
        """stdout に出す人の読む形。"""
        return "\n".join(
            (
                f"{self.stage} {self.package}(木 {self.tree})",
                *self._children_lines(),
                *self._rewrite_lines(),
                *([f"消した移し替えの module: {', '.join(self.removed)}"] if self.removed else []),
                f"残った古い path: {sum(self.remaining.values())} か所({len(self.remaining)} file)"
                + "".join(f"\n  {path} {count}" for path, count in sorted(self.remaining.items())),
                *self._table_lines(),
                *(f"ぶつかり: {c}" for c in self.conflicts),
                *(f"注: {n}" for n in self.notes),
            )
        )

    def _children_lines(self) -> tuple[str, ...]:
        children = "、".join(
            f"{c['old']} → {c['new']}(module {c['modules']})" for c in self.children
        )
        names = "・".join(f"{label} {len(found)}" for label, found in self.names.items() if found)
        total = sum(len(found) for found in self.names.values())
        return (
            *([f"子: {len(self.children)} 個 — {children}"] if self.children else []),
            *([f"名(前): {total} 個 — {names}"] if self.names else []),
            *(
                [f"入口へ足した名: {len(self.entry_added)} 個 — {', '.join(self.entry_added)}"]
                if self.entry_added
                else []
            ),
        )

    def _rewrite_lines(self) -> tuple[str, ...]:
        summary = self.rewrite
        if summary is None:
            return ()
        parts = "・".join(f"{label} {count}" for label, count in sorted(summary.rewritten.items()))
        outliers = summary.outliers_to_underscore
        listed = f": {', '.join(outliers)}" if outliers else ""
        return (
            f"古い path を読む場所(前): {summary.places_before} か所",
            f"書き換えた場所(後): {sum(summary.rewritten.values())} か所"
            f"({summary.files_changed} file)— {parts}",
            f"入口へ向けた名: {summary.names_to_entry}・_ の path へ向けた名: {summary.names_to_underscore}"
            f"(うち既知の表の外れの名 {len(outliers)}{listed})",
        )

    def _table_lines(self) -> tuple[str, ...]:
        def groups(counts: dict[str, int]) -> str:
            return "・".join(f"{group} {count}" for group, count in sorted(counts.items()))

        return (
            *(
                [f"既知の表から消した行: {groups(self.table_rows_removed)}"]
                if self.table_rows_removed
                else []
            ),
            *(
                [
                    f"既知の表に残した隠した module の行(入口の名と同じと読む行): {groups(self.table_rows_left)}"
                ]
                if self.table_rows_left
                else []
            ),
        )


def _load_table(path: Path) -> dict[str, object] | None:
    """既知の表(JSON)を読む。表の無い木(外の repo・使い捨ての木)は None — 名の分けは種類だけで決める。"""
    if not path.is_file():
        return None
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _outliers(
    result: TreeResult, children: Sequence[Child], groups: dict[tuple[str, str], str]
) -> tuple[str, ...]:
    """呼び手が ``_`` の path から読む名のうち、既知の表の組「外れ」の名(card が 1 名ずつ読み分ける為)。"""
    olds = (
        (child.old + module[len(child.new) :], name)
        for (module, name), count in result.counts.to_underscore.items()
        if count > 0
        for child in children
        if module == child.new or module.startswith(child.new + ".")
    )
    return tuple(
        sorted(
            {
                f"{old}.{name}"
                for old, name in olds
                if groups.get((old, name), "").startswith("外れ")
            }
        )
    )


def _summary(
    result: TreeResult, children: Sequence[Child], groups: dict[tuple[str, str], str]
) -> RewriteSummary:
    """木の書き換えの結末から、報告の数(前と後)。"""
    return RewriteSummary(
        places_before=result.before,
        rewritten=dict(result.counts.places),
        files_changed=len(result.changed),
        names_to_entry=sum(c for c in result.counts.to_entry.values() if c > 0),
        names_to_underscore=sum(c for c in result.counts.to_underscore.values() if c > 0),
        outliers_to_underscore=_outliers(result, children, groups),
    )


def _unread_notes(result: TreeResult) -> tuple[str, ...]:
    """構文か字句を読めなかった file の注(その file の import の文は分けていない)。"""
    if not result.unread:
        return ()
    return (
        f"構文か字句を読めず、import の文を分けずに module path と file の path だけを書き換えた file "
        f"{len(result.unread)} 個: {', '.join(result.unread)}",
    )


def _tree_of(
    tree: Path, layout: _Layout, underscore_only: Sequence[str], kept: Iterable[str]
) -> Tree:
    """書き換える木: package の import の根が木の中に在れば、その下を ``_`` の path だけを読む所にする。"""
    roots = {
        root.path.resolve().relative_to(tree.resolve()).as_posix(): root.name
        for root in layout.roots
        if root.path.resolve().is_relative_to(tree.resolve())
    }
    return Tree(tree, tuple(underscore_only), frozenset(kept), roots)


def _module_count(child: Child, stem: str) -> int:
    """子の module の数(報告の為)。"""
    return sum(
        sum(
            1
            for p in path.rglob("*")
            if p.suffix in (".py", ".hy") and "__pycache__" not in p.parts
        )
        if path.is_dir()
        else int(path.suffix in (".py", ".hy"))
        for path in child.sources(stem)
    )


def _move(tree: Path, child: Child) -> tuple[str, ...]:
    """子を ``git mv`` で ``_`` の名へ移す。git の追跡しない file が残れば注を返す。"""
    notes = tuple(note for source in child.sources(child.name) if (note := _move_one(tree, source)))
    return notes


def _move_one(tree: Path, source: Path) -> str:
    _git(tree, "mv", str(source), str(source.with_name("_" + source.name)))
    if not source.is_dir():
        return ""
    left = [p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    if left:
        return f"{source}: git の追跡しない file が残った({len(left)} 個)— 移していない"
    shutil.rmtree(source)  # 残ったのは __pycache__ だけ
    return ""


def _name_notes(fates: Sequence[NameFate]) -> tuple[str, ...]:
    unlisted = [f"{f.modules[0]}.{f.name}" for f in fates if f.note.startswith("既知の表に無い")]
    macros = [f"{f.modules[0]}.{f.name}" for f in fates if f.fate == "macro"]
    return (
        *(
            [f"既知の表に無い effect と handler の外の名を _ に残した: {', '.join(unlisted)}"]
            if unlisted
            else []
        ),
        *(
            [f"Hy の macro は入口から出せない(require は _ の path を読む): {', '.join(macros)}"]
            if macros
            else []
        ),
    )


def hide(
    package_dir: Path,
    names: Sequence[str],
    tree_path: Path | None,
    table_path: Path | None,
    underscore_only: Sequence[str],
) -> Report:
    """stage 1: 子を ``_`` へ移し、移し替えの module を置き、入口へ名を足し、木の import を書き換える。"""
    tool = _inventory_tool()
    package_dir = package_dir.resolve()
    tree = (tree_path or git_toplevel(package_dir)).resolve()
    layout = tool.read_layout(package_dir)
    children = find_children(layout, names, hidden=False)
    taken = [c.new for c in children if c.sources(f"_{c.name}")]
    if taken:
        raise SystemExit(f"既に在る: {', '.join(taken)}")
    root_dirs = {c.root: c.root_dir for c in children}
    shapes = {
        root: EntryShape.read(directory / "__init__.py") for root, directory in root_dirs.items()
    }
    problems = [p for p in (s.problem() for s in shapes.values()) if p]
    if problems:
        raise SystemExit("\n".join(problems))
    [package] = tool.inventory(package_dir.parent.parent, [package_dir.name], 1, 1800)
    if package["error"] is not None:
        raise SystemExit(f"目録が {package_dir.name} を読めなかった: {package['error']}")
    table = _load_table(table_path or tree / DEFAULT_TABLE)
    groups, added = _table_groups(table, package_dir.name)
    fates = decide_names(cast(list[ExportRow], package["exports"]), children, groups, added)
    counted = tuple(
        {"old": c.old, "new": c.new, "modules": _module_count(c, c.name)} for c in children
    )
    moved = tuple(note for child in children for note in _move(tree, child))
    for child in children:
        child.shim.write_text(
            shim_text(child, _typed(child.root_dir, child.root, child.new)), encoding="utf-8"
        )
        _git(tree, "add", str(child.shim))
    entries = {
        root: update_entry(
            shape,
            root,
            [f for f in fates if f.source is not None and f.source.split(".")[0] == root],
            [c for c in children if c.root == root],
            root_dirs[root],
        )
        for root, shape in shapes.items()
    }
    created = tuple(
        shape.path for shape in shapes.values() if shape.tree is None and shape.path.is_file()
    )
    for path in created:
        _git(tree, "add", str(path))
    kept = {
        DEFAULT_TABLE.as_posix(),
        *(c.shim.resolve().relative_to(tree).as_posix() for c in children),
    }
    result = rewrite_tree(
        _tree_of(tree, layout, underscore_only, kept),
        Patterns.of(children),
        same_as_entry(layout, children),
        write=True,
    )
    return Report(
        "hide",
        package_dir.name,
        str(tree),
        children=counted,
        names={
            label: [f"{f.modules[0]}.{f.name}" for f in fates if f.fate == fate]
            for fate, label in FATE_LABELS.items()
        },
        entry_added=tuple(
            name for added_names, _skipped in entries.values() for name in added_names
        ),
        conflicts=(
            *(f"{f.modules[0]}.{f.name}: {f.note}" for f in fates if f.fate == "conflict"),
            *(
                f"{root}.{name}: 入口に同じ名が既に在る(足さない)"
                for root, (_added, skipped) in entries.items()
                for name in skipped
            ),
        ),
        notes=(
            *_name_notes(fates),
            *moved,
            *(f"入口の無い名前空間 package に入口を作った: {path}" for path in created),
            *_unread_notes(result),
        ),
        rewrite=_summary(result, children, groups),
        remaining=result.remaining,
    )


def rewrite(
    package_dir: Path,
    names: Sequence[str],
    tree_path: Path,
    table_path: Path | None,
    underscore_only: Sequence[str],
    dry_run: bool,
) -> Report:
    """stage 2: 隠した後の package に合わせて、木(外の repo の作業木)の import を書き換える。"""
    tool = _inventory_tool()
    package_dir = package_dir.resolve()
    layout = tool.read_layout(package_dir)
    children = find_children(layout, names, hidden=True)
    tree = tree_path.resolve()
    doeff_tree = git_toplevel(package_dir)
    groups, _added = _table_groups(
        _load_table(table_path or doeff_tree / DEFAULT_TABLE), package_dir.name
    )
    kept = {
        *({DEFAULT_TABLE.as_posix()} if tree == doeff_tree else set()),
        *(
            c.shim.resolve().relative_to(tree).as_posix()
            for c in children
            if c.shim.resolve().is_relative_to(tree)
        ),
    }
    result = rewrite_tree(
        _tree_of(tree, layout, underscore_only, kept),
        Patterns.of(children),
        same_as_entry(layout, children),
        write=not dry_run,
    )
    return Report(
        "rewrite" + ("(書かない)" if dry_run else ""),
        package_dir.name,
        str(tree),
        children=tuple(
            {"old": c.old, "new": c.new, "modules": _module_count(c, f"_{c.name}")}
            for c in children
        ),
        notes=_unread_notes(result),
        rewrite=_summary(result, children, groups),
        remaining=result.remaining,
    )


def finish(
    package_dir: Path, names: Sequence[str], tree_path: Path | None, table_path: Path | None
) -> Report:
    """stage 3: 移し替えの module を消し、木に残った古い path を数え、既知の表から消えた古い名の行を消す。"""
    tool = _inventory_tool()
    package_dir = package_dir.resolve()
    layout = tool.read_layout(package_dir)
    children = find_children(layout, names, hidden=True)
    tree = (tree_path or git_toplevel(package_dir)).resolve()
    strangers = [str(c.shim) for c in children if not is_shim(c.shim, c)]
    if strangers:
        raise SystemExit(
            f"移し替えの module でない(1 行目に {SHIM_MARK} の印が無い): {', '.join(strangers)}"
        )
    for child in children:
        # 1 行目の印で道具が置いた file と確かめた — commit 前の(index にだけ在る)移し替えの module も消す。
        _git(tree, "rm", "-q", "-f", str(child.shim))
    patterns = Patterns.of(children)
    candidates = _tree_of(tree, layout, (), {DEFAULT_TABLE.as_posix()}).candidates(patterns)
    remaining = {
        relative: left
        for relative, kind in candidates
        if (
            left := old_references(
                (tree / relative).read_text(encoding="utf-8"), patterns, kind in ("hy", "markdown")
            )
        )
    }
    table = _finish_table(tool, package_dir, children, table_path or tree / DEFAULT_TABLE)
    return Report(
        "finish",
        package_dir.name,
        str(tree),
        removed=tuple(c.shim.resolve().relative_to(tree).as_posix() for c in children),
        remaining=remaining,
        table_rows_removed=table.removed,
        table_rows_left=table.left,
        notes=table.notes,
    )


@dataclass(frozen=True)
class _TableResult:
    """stage 3 の既知の表の直し: 組ごとに消した行・残した隠した module の行・注。"""

    removed: dict[str, int] = field(default_factory=dict)
    left: dict[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def _finish_table(
    tool: _Inventory, package_dir: Path, children: Sequence[Child], path: Path
) -> _TableResult:
    """移し替えの module を消した後の目録と既知の表を突き合わせ、隠した子の消えた行を表から消す。"""
    table = _load_table(path)
    if table is None or package_dir.name not in cast(dict[str, object], table["packages"]):
        return _TableResult(notes=(f"既知の表 {path} に {package_dir.name} が無い — 表は触らない",))
    [package] = tool.inventory(package_dir.parent.parent, [package_dir.name], 1, 1800)
    if package["error"] is not None:
        return _TableResult(
            notes=(f"目録が {package_dir.name} を読めなかった — 表は触らない: {package['error']}",)
        )
    stale = {
        key for key in stale_rows(package, table) if _row_is_hidden(key, children, tool.SUBMODULE)
    }
    removed = drop_rows(path, table, package_dir.name, stale)
    groups, _added = _table_groups(table, package_dir.name)
    left = collections.Counter(
        group for key, group in groups.items() if _row_is_hidden(key, children, tool.SUBMODULE)
    )
    return _TableResult(removed=removed, left=dict(left))


def _table_guard() -> ModuleType:
    """既知の表の test の module(表と目録を突き合わせる関数の 1 か所 — ADR-DOE-API-001 R12)。"""
    loaded = sys.modules.get("_hide_submodules_table_guard")
    if loaded is None:
        spec = importlib.util.spec_from_file_location(
            "_hide_submodules_table_guard", ROOT / "tests" / "test_public_api_known_violations.py"
        )
        assert spec is not None
        assert spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        sys.modules["_hide_submodules_table_guard"] = loaded
        spec.loader.exec_module(loaded)
    return loaded


def stale_rows(package: dict[str, object], table: dict[str, object]) -> set[tuple[str, str]]:
    """表に在るのに目録に無い行(入口へ移した名と同じと読む行を除く — 表の test と同じ突き合わせ)。"""
    guard = _table_guard()
    found = guard.outside_rows(package)
    known = guard.known_names(table, package["dir"])
    moved = guard.moved_to_root(package["import_roots"], found, known)
    return cast(set[tuple[str, str]], known - set(found) - {old for _root, old in moved})


def _row_is_hidden(key: tuple[str, str], children: Sequence[Child], submodule: str) -> bool:
    """表の行が隠した子の行か(子とその下の submodule の行・名を出す module が子かその下の行)。"""
    module, name = key
    return _hidden_target(name if module == submodule else module, children) is not None


def drop_rows(
    path: Path, table: dict[str, object], package_dir: str, rows: set[tuple[str, str]]
) -> dict[str, int]:
    """表から行を消して書く(表は減るだけ — 組ごとに消した数を返す)。空になった module と組も消す。"""
    entry = cast(dict[str, dict[str, dict[str, list[str]]]], table["packages"])[package_dir]
    removed = collections.Counter(
        {
            group: sum(
                1 for module, found in modules.items() for name in found if (module, name) in rows
            )
            for group, modules in entry["名"].items()
        }
    )
    entry["名"] = {
        group: kept_modules
        for group, modules in entry["名"].items()
        if (
            kept_modules := {
                module: kept
                for module, found in modules.items()
                if (kept := [name for name in found if (module, name) not in rows])
            }
        )
    }
    added = cast(list[dict[str, str]], table.get("足した名", []))
    left = [r for r in added if r["package"] != package_dir or (r["module"], r["name"]) not in rows]
    removed["足した名"] = len(added) - len(left)
    table["足した名"] = left
    if removed.total():
        text = path.read_text(encoding="utf-8")
        second = text.split("\n", 2)[1] if text.count("\n") > 1 else "  "
        indent = len(second) - len(second.lstrip(" ")) or 2
        path.write_text(
            json.dumps(table, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8"
        )
    return {group: count for group, count in removed.items() if count}


# --- 命令 ---


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    parser.add_argument("--identity", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--identity-out", type=Path, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="stage")
    for stage in ("hide", "rewrite", "finish"):
        command = commands.add_parser(stage)
        command.add_argument("--package", type=Path, required=True)
        command.add_argument("--child", action="append", required=True, dest="children")
        command.add_argument("--tree", type=Path, required=stage == "rewrite")
        command.add_argument("--table", type=Path)
        command.add_argument("--json")
        if stage != "finish":
            command.add_argument("--underscore-only", action="append", default=[])
        if stage == "rewrite":
            command.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.identity is not None:
        payload = cast(_IdentityPayload, json.loads(arguments.identity.read_text(encoding="utf-8")))
        arguments.identity_out.write_text(json.dumps(identity_probe(payload)), encoding="utf-8")
        return 0
    if arguments.stage == "hide":
        report = hide(
            arguments.package,
            arguments.children,
            arguments.tree,
            arguments.table,
            arguments.underscore_only,
        )
    elif arguments.stage == "rewrite":
        report = rewrite(
            arguments.package,
            arguments.children,
            arguments.tree,
            arguments.table,
            arguments.underscore_only,
            arguments.dry_run,
        )
    elif arguments.stage == "finish":
        report = finish(arguments.package, arguments.children, arguments.tree, arguments.table)
    else:
        parser.print_help()
        return 2
    print(report.text())
    if arguments.json is not None:
        document = json.dumps(report.as_json(), ensure_ascii=False, indent=2) + "\n"
        if arguments.json == "-":
            sys.stdout.write(document)
        else:
            Path(arguments.json).write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
