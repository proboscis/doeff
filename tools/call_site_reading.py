"""呼び手を書き換える道具が共に使う読みの部品: 字の範囲の書き換え・Python の字の位置・Hy の字句の木・markdown の囲み・木の file。

使う道具: tools/former_factory_call_rewrite.py(``tools/`` は package ではないので、道具が file の path で読む)。
ここには名ごとの形の読み(どの呼びを書き換えるか)を置かない — それは道具の側に在る。

見る file(git の追跡する file): Python(``.py``・``.pyi``)・Hy(``.hy``・``.hyk``・``.hyp``)・markdown(``.md`` の code
の囲み)。歴史の記録(``docs/design``・``docs/design-checks``・``docs/history``・``publications``・``evidence`` の dir・
``CHANGELOG.md``)は数えるだけで書き換えない(tools/hide_submodules.py と同じ)。
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import Literal

#: 歴史として書き換えない所(木の根からの相対の頭・dir の名・file の名)。tools/hide_submodules.py と同じ。
HISTORY_PREFIXES = ("docs/design/", "docs/design-checks/", "docs/history/", "publications/")
HISTORY_DIR_NAMES = frozenset({"evidence"})
HISTORY_FILE_NAMES = frozenset({"CHANGELOG.md"})
#: 区画を 2 段目の dir で分ける根の dir。
SECTION_ROOTS = frozenset({"packages", "controllers", "services"})

PYTHON_SUFFIXES = (".py", ".pyi")
HY_SUFFIXES = (".hy", ".hyk", ".hyp")
MARKDOWN_SUFFIXES = (".md",)
PYTHON_FENCES = frozenset({"python", "py", "python3"})
HY_FENCES = frozenset({"hy", "hylang", "clojure", "clj", "lisp"})


@dataclass(frozen=True)
class Edit:
    """text の字の範囲 [start, end) を ``text`` に替える書き換え 1 つ。"""

    start: int
    end: int
    text: str

    def shifted(self, by: int) -> Edit:
        """囲み・文字列の中で読んだ書き換えを、file の字の位置へ移す。"""
        return Edit(self.start + by, self.end + by, self.text)


@dataclass(frozen=True)
class Site:
    """名を読む所 1 つ: 形・行・書き換え(書き換えない形は空)・手で直す訳。"""

    form: str
    line: int
    edits: tuple[Edit, ...] = ()
    reason: str = ""

    def moved(self, lines: int, chars: int) -> Site:
        """囲み・文字列の中で読んだ所を、file の行と字の位置へ移す。"""
        edits = tuple(edit.shifted(chars) for edit in self.edits)
        return Site(self.form, self.line + lines, edits, self.reason)


# --- 字の範囲 ---


def _line_starts(text: str) -> list[int]:
    """各行の頭の字の位置(ast の行と列を字の位置にするため)。"""
    return [0, *(index + 1 for index, char in enumerate(text) if char == "\n")]


def line_of(text: str, offset: int) -> int:
    """字の位置の行(1 から — 所を人に見せる行の番号)。"""
    return text.count("\n", 0, offset) + 1


def apply_edits(text: str, edits: Iterable[Edit]) -> str:
    """重ならない書き換えを後ろから当てる。"""
    ordered = sorted(edits, key=lambda e: (e.start, e.end), reverse=True)
    return reduce(lambda done, e: done[: e.start] + e.text + done[e.end :], ordered, text)


def _span(edits: Sequence[Edit]) -> tuple[int, int]:
    """書き換えの組が覆う字の範囲(重なりを判じるため)。"""
    return min(e.start for e in edits), max(e.end for e in edits)


def disjoint(sites: Iterable[Site]) -> tuple[Site, ...]:
    """書き換えの在る所のうち、範囲の重ならない物を外側から選ぶ(内側は次の回に読み直して当てる)。"""
    ordered = sorted(
        (site for site in sites if site.edits),
        key=lambda site: (_span(site.edits)[0], -_span(site.edits)[1]),
    )

    def take(chosen: tuple[Site, ...], site: Site) -> tuple[Site, ...]:
        start, end = _span(site.edits)
        overlaps = any(start < _span(c.edits)[1] and _span(c.edits)[0] < end for c in chosen)
        return chosen if overlaps else (*chosen, site)

    return reduce(take, ordered, ())


# --- Python ---


def dotted(node: ast.AST) -> str | None:
    """``a.b.c`` の名と属性の式を点の名で読む(module の別名の属性を読むため・ほかの式は None)。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def rebinding(node: ast.AST, names: frozenset[str]) -> str | None:
    """node が名 ``names`` のどれかを定義し直すなら、その訳。"""
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in names:
        return f"{node.lineno} 行目で {node.id} に値を入れ直す"
    if isinstance(node, ast.arg) and node.arg in names:
        return f"{node.lineno} 行目の引数の名が {node.arg}"
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name in names
    ):
        return f"{node.lineno} 行目で {node.name} を定義し直す"
    return None


class PythonSource:
    """Python の text と、ast の (行, UTF-8 の byte の列) を字の位置にする表(日本語の comment の在る行でもずれない)。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = _line_starts(text)

    def _offset(self, line: int, byte_col: int) -> int:
        head = self.starts[line - 1]
        end = self.starts[line] if line < len(self.starts) else len(self.text)
        return head + len(self.text[head:end].encode()[:byte_col].decode())

    def start(self, node: ast.AST) -> int:
        return self._offset(node.lineno, node.col_offset)  # type: ignore[attr-defined]

    def end(self, node: ast.AST) -> int:
        return self._offset(node.end_lineno, node.end_col_offset)  # type: ignore[attr-defined]

    def segment(self, node: ast.AST) -> str:
        return self.text[self.start(node) : self.end(node)]


def string_body(source: PythonSource, node: ast.Constant) -> int | None:
    """文字列の literal の中身が値と同じ字で書いてあれば、中身の頭の字の位置(escape の在る literal は None)。

    頭の ``\\`` と改行(``\"\"\"\\``)だけは読み飛ばす: 中身は値と同じ字になる。
    """
    segment = source.segment(node)
    match = re.match(r"(?i)[rbu]{0,2}(\"\"\"|'''|\"|')", segment)
    if match is None or not segment.endswith(match.group(1)):
        return None
    head = match.end()
    raw = "r" in match.group(0).lower()
    if not raw and segment.startswith("\\\n", head):
        head += 2
    body = segment[head : len(segment) - len(match.group(1))]
    if body != node.value:
        return None
    return source.start(node) + head


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
    """Hy の text の形 1 つ(字の範囲つき): 括弧の形・atom・文字列。tools/hide_submodules.py と同じ読み。"""

    kind: Literal["form", "atom", "string"]
    start: int
    end: int
    opener: str = ""
    text: str = ""
    children: tuple[HyNode, ...] = ()


class HyUnreadableError(Exception):
    """Hy の字句として読めない text(閉じない括弧・開かない閉じ括弧・字句の外の字)。"""


def _hy_tokens(text: str) -> Iterator[re.Match[str]]:
    position = 0
    while position < len(text):
        match = _HY_TOKEN.match(text, position)
        if match is None:
            raise HyUnreadableError(f"{position} 字目を字句に読めない")
        position = match.end()
        if match.lastgroup not in ("space", "comment"):
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


def parse_hy(text: str) -> tuple[HyNode, ...]:
    """Hy の text を形の木に読む(読めなければ HyUnreadableError)。"""
    tokens = tuple(_hy_tokens(text))
    nodes, index = _hy_forms(tokens, 0)
    if index != len(tokens):
        raise HyUnreadableError(f"{tokens[index].start()} 字目の閉じ括弧が開いていない")
    return nodes


def walk(nodes: Iterable[HyNode]) -> Iterator[HyNode]:
    """形の木の全部の形を、外から内の順に(import と呼びを木のどこからでも拾うため)。"""
    for node in nodes:
        yield node
        yield from walk(node.children)


def hy_head(node: HyNode) -> str | None:
    """``(head …)`` の head の atom(括弧の形でなければ None)。"""
    if node.kind != "form" or node.opener != "(" or not node.children:
        return None
    first = node.children[0]
    return first.text if first.kind == "atom" else None


#: 名を定義する形の head(名・引数の list の名が書き換える名の atom なら、その file は手で直す)。
_HY_DEFINERS = frozenset({"defn", "defn/a", "defk", "deff", "defp", "defmacro"})


def bound_names(node: HyNode) -> list[str]:
    """形 1 つが名に使う atom(``(setv a 1 b 2)``・``(fn [a] …)``・``(let [a 1] …)``・``(defn f [a] …)``)。"""
    head = hy_head(node)
    rest = node.children[1:]
    if head == "setv":
        return [c.text for c in rest[::2] if c.kind == "atom"]
    if head in ("fn", "fn/a", "let") and rest and rest[0].opener == "[":
        return [c.text for c in rest[0].children[:: 2 if head == "let" else 1] if c.kind == "atom"]
    if head in _HY_DEFINERS:
        params = [c for c in rest[:3] if c.kind == "form" and c.opener == "["]
        return [c.text for c in rest[:2] if c.kind == "atom"] + [
            p.text for form in params for p in form.children if p.kind == "atom"
        ]
    return []


# --- markdown ---

_FENCE = re.compile(r"^[ \t]*(?P<fence>```+|~~~+)[ \t]*(?P<lang>[\w+-]*)[^\n]*\n", re.MULTILINE)


def fences(text: str) -> Iterator[tuple[str, int, int]]:
    """markdown の code の囲みごとに (言語, 中身の頭, 中身の終わり)。"""
    position = 0
    while (opening := _FENCE.search(text, position)) is not None:
        fence = opening.group("fence")
        closing = re.compile(rf"^[ \t]*{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$", re.MULTILINE)
        end = closing.search(text, opening.end())
        if end is None:
            return
        yield opening.group("lang").lower(), opening.end(), end.start()
        position = end.end()


# --- 木 ---


def is_history(path: str) -> bool:
    """歴史の記録の file か(数えるだけで書き換えない)。"""
    parts = Path(path).parts
    return (
        path.startswith(HISTORY_PREFIXES)
        or any(part in HISTORY_DIR_NAMES for part in parts[:-1])
        or parts[-1] in HISTORY_FILE_NAMES
    )


def section_of(path: str) -> str:
    """区画: ``packages/<名>``・``controllers/<名>``・``services/<名>``、ほかは木の根のすぐ下の dir(根の file は ``.``)。"""
    parts = Path(path).parts
    if len(parts) == 1:
        return "."
    if parts[0] in SECTION_ROOTS and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _git(tree: Path, *args: str, stdin: str | None = None) -> str:
    """木の git を 1 回呼んで stdout を読む(追跡する file と版の木を並べるため)。"""
    return subprocess.run(
        ["git", "-C", str(tree), *args], check=True, capture_output=True, text=True, input=stdin
    ).stdout


def _wanted(path: str, prefixes: Sequence[str]) -> bool:
    """読む file か: 言語の拡張子を持ち、``--path`` の頭のどれかの下に在る(頭が無ければ全部)。"""
    suffixes = PYTHON_SUFFIXES + HY_SUFFIXES + MARKDOWN_SUFFIXES
    return path.endswith(suffixes) and (
        not prefixes or any(path.startswith(prefix.rstrip("/")) for prefix in prefixes)
    )


def tree_texts(tree: Path, prefixes: Sequence[str], rev: str | None) -> Iterator[tuple[str, str]]:
    """(木からの相対の path, 中身) の列: 作業木の追跡する file か、git の版 ``rev`` の file。"""
    if rev is None:
        for path in sorted(
            p for p in _git(tree, "ls-files", "-z").split("\0") if _wanted(p, prefixes)
        ):
            file = tree / path
            if file.is_file():
                yield path, file.read_text(encoding="utf-8", errors="replace")
        return
    listed = _git(tree, "ls-tree", "-r", "-z", "--name-only", rev).split("\0")
    paths = sorted(p for p in listed if _wanted(p, prefixes))
    # 1 回の cat-file --batch で全部読む(file ごとに git を起こさない)。
    raw = subprocess.run(
        ["git", "-C", str(tree), "cat-file", "--batch"],
        check=True,
        capture_output=True,
        input="".join(f"{rev}:{p}\n" for p in paths).encode(),
    ).stdout
    position = 0
    for path in paths:
        header_end = raw.index(b"\n", position)
        size = int(raw[position:header_end].split()[2])
        body = raw[header_end + 1 : header_end + 1 + size]
        position = header_end + 1 + size + 1
        yield path, body.decode("utf-8", errors="replace")
