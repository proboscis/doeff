"""``scheduled(p)`` を handler を返す工場の形 ``scheduled(implementation=…)(p)`` へ書き換える道具。

決まり: ADR-DOE-API-001 R6(2026-10-28 の決定 ci-TDTZ23GS70F1VFM0DV183G9VQQ〜ci-AFX0HEJAT7SAMANA3YCPKE9RJA)— ``scheduled`` は
中核の ``state()`` と同じ「handler を返す工場」。``scheduled()`` は印の付いた handler を返し、旧い形 ``scheduled(p)`` は呼び手を
書き換え終えるまでの移し替えの層として ``scheduled()(p)`` と同じに動く(agora card ki-f2c2f789f5bb)。

使い方(doeff の作業木で)::

    uv run python tools/scheduled_factory_rewrite.py census --tree ~/repos/agora-controllers --rev origin/main
    uv run python tools/scheduled_factory_rewrite.py rewrite --tree . --path packages/doeff-hy

- ``census``: 呼び方の形ごとの数を区画(``packages/<名>``・``controllers/<名>``・``services/<名>`` か木の根のすぐ下の dir)
  ごとに出す。``--rev`` を渡すと作業木でなく git の版の中身を読む(共有の checkout を触らずに数える)。``--files`` で file
  ごとの数も、``--values`` で値として渡す所の行も出す。
- ``rewrite``: 作業木を書き換える。書き換えない所(``手で直す``・``値として渡す``)は場所と訳を出して残す。``--dry-run`` は
  書かずに数だけ出す。
- ``--path``(何度でも): 木からの相対の頭で、見る file を絞る。``--json <file>``(``-`` は stdout): 結果を JSON で書く。

見る file(git の追跡する file): Python(``.py``・``.pyi``)・Hy(``.hy``・``.hyk``・``.hyp``)・markdown(``.md`` の code
の囲み)。Python の文字列の literal に書いた Hy(test の中の Hy の text)も Hy として読む。歴史の記録(``docs/design``・
``docs/design-checks``・``docs/history``・``publications``・``evidence`` の dir・``CHANGELOG.md``)は数えるだけで書き換えない。
印 ``scheduled-factory-rewrite: keep`` を書いた file(この道具と、その test — 旧い形をわざと書いた入力と移し替えの層の確かめ)は
旧い形の数に入れず書き換えない。移し替えの層を消す時に、印の file の旧い形も一緒に消す。

どの名が doeff の ``scheduled`` か: file の import が読む名(``from doeff_core_effects import scheduled as s``・
``(import doeff_core_effects.scheduler [scheduled])``・module の別名の ``m.scheduled``)。同じ名を定義し直す file
(``scheduled = …``・引数の名)は、その file の旧い形を全部 ``手で直す`` に数える。markdown の囲みは、囲みが import しなくても、
file が ``doeff_core_effects`` を書いていれば ``scheduled`` を doeff の物と読む。

形(census の鍵)と書き換え:

- 呼び — program を位置で渡す: ``scheduled(p)`` -> ``scheduled()(p)``・``(scheduled p)`` -> ``((scheduled) p)``
- 呼び — program と implementation: ``scheduled(p, implementation=i)`` -> ``scheduled(implementation=i)(p)``・
  ``(scheduled p :implementation i)`` -> ``((scheduled :implementation i) p)``
- 呼び — program を keyword で渡す: ``scheduled(body_program=p)`` -> ``scheduled()(p)``
- threading macro の段: ``(-> p (scheduled))`` -> ``(-> p ((scheduled)))``・``(-> p scheduled)`` -> ``(-> p ((scheduled)))``
- partial: ``partial(scheduled, implementation=i)`` -> ``scheduled(implementation=i)``
- 値として渡す(書き換えない・場所を出す): ``{"scheduled": scheduled}``(Hy の名前空間)・``((handles …) scheduled)``
  (domain の宣言)など、工場そのものを名指す所。名 ``scheduled`` は新しい形でも工場なので、そのままで正しい。program を
  受ける関数として渡す所(``wrap=scheduled``)だけは ``scheduled()`` に手で直す。
- 呼び — 新しい形: ``scheduled()``・``scheduled(implementation=i)``(書き換えない)
- 属性を読む: ``scheduled.__doc__`` など(書き換えない)
- 手で直す: ``*args``・``**kw``・知らない keyword・program を 2 つ以上渡す・名を定義し直す file・読めない file・
  escape の在る文字列の中の Hy

書き換えは字の範囲で当て(書いた形と comment を崩さない)、重なる所は外側を先に当てて読み直し、書き換える所が無くなるまで繰り返す。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import Literal

#: doeff の ``scheduled`` を出す module(``doeff_core_effects.scheduler`` は ``_scheduler`` への移し替えの module)。
SCHEDULER_MODULES = frozenset(
    {"doeff_core_effects", "doeff_core_effects.scheduler", "doeff_core_effects._scheduler"}
)
NAME = "scheduled"

#: 歴史として書き換えない所(木の根からの相対の頭・dir の名・file の名)。tools/hide_submodules.py と同じ。
HISTORY_PREFIXES = ("docs/design/", "docs/design-checks/", "docs/history/", "publications/")
HISTORY_DIR_NAMES = frozenset({"evidence"})
HISTORY_FILE_NAMES = frozenset({"CHANGELOG.md"})
#: 旧い形をわざと書いた file の印(この file 自身も持つ)。
KEEP_MARK = "scheduled-factory-rewrite: keep"
#: 区画を 2 段目の dir で分ける根の dir。
SECTION_ROOTS = frozenset({"packages", "controllers", "services"})

PYTHON_SUFFIXES = (".py", ".pyi")
HY_SUFFIXES = (".hy", ".hyk", ".hyp")
MARKDOWN_SUFFIXES = (".md",)
_PYTHON_FENCES = frozenset({"python", "py", "python3"})
_HY_FENCES = frozenset({"hy", "hylang", "clojure", "clj", "lisp"})

POSITIONAL = "呼び — program を位置で渡す"
WITH_IMPLEMENTATION = "呼び — program と implementation"
KEYWORD_PROGRAM = "呼び — program を keyword で渡す"
THREADED = "threading macro の段"
PARTIAL = "partial"
VALUE = "値として渡す"
NEW_FORM = "呼び — 新しい形"
ATTRIBUTE = "属性を読む"
MANUAL = "手で直す"
FORMS = (
    POSITIONAL,
    WITH_IMPLEMENTATION,
    KEYWORD_PROGRAM,
    THREADED,
    PARTIAL,
    MANUAL,
    VALUE,
    NEW_FORM,
    ATTRIBUTE,
)
#: 旧い形(道具が書き換える残り)。移し替えの層は、これと ``手で直す`` がどの repo でも 0 になってから消す。
OLD_FORMS = frozenset({POSITIONAL, WITH_IMPLEMENTATION, KEYWORD_PROGRAM, THREADED, PARTIAL})

Language = Literal["Python", "Hy", "Python の文字列の Hy", "markdown の Python", "markdown の Hy"]


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
    """``scheduled`` を読む所 1 つ: 形・行・書き換え(書き換えない形は空)・手で直す訳。"""

    form: str
    line: int
    edits: tuple[Edit, ...] = ()
    reason: str = ""

    def moved(self, lines: int, chars: int) -> Site:
        """囲み・文字列の中で読んだ所を、file の行と字の位置へ移す。"""
        edits = tuple(edit.shifted(chars) for edit in self.edits)
        return Site(self.form, self.line + lines, edits, self.reason)


@dataclass(frozen=True)
class FileReport:
    """file 1 つの読み: 言語つきの所の列と、歴史の記録か・残す印の file か(どちらも書き換えない)。"""

    path: str
    history: bool
    kept: bool
    sites: tuple[tuple[Language, Site], ...]

    @property
    def left(self) -> bool:
        """書き換えず、旧い形の数にも入れない file か。"""
        return self.history or self.kept


# --- 字の範囲 ---


def _line_starts(text: str) -> list[int]:
    """各行の頭の字の位置(ast の行と列を字の位置にするため)。"""
    return [0, *(index + 1 for index, char in enumerate(text) if char == "\n")]


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _apply(text: str, edits: Iterable[Edit]) -> str:
    """重ならない書き換えを後ろから当てる。"""
    ordered = sorted(edits, key=lambda e: (e.start, e.end), reverse=True)
    return reduce(lambda done, e: done[: e.start] + e.text + done[e.end :], ordered, text)


def _span(edits: Sequence[Edit]) -> tuple[int, int]:
    return min(e.start for e in edits), max(e.end for e in edits)


def _disjoint(sites: Iterable[Site]) -> tuple[Site, ...]:
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


@dataclass(frozen=True)
class PythonNames:
    """Python の file で doeff の ``scheduled`` を指す名(``scheduled`` か別名)と、それを持つ module の別名。"""

    names: frozenset[str]
    modules: frozenset[str]

    def matches(self, node: ast.AST) -> bool:
        """node が doeff の ``scheduled`` を読む式か(``scheduled``・``m.scheduled``)。"""
        if isinstance(node, ast.Name):
            return node.id in self.names
        return (
            isinstance(node, ast.Attribute)
            and node.attr == NAME
            and _dotted(node.value) in self.modules
        )


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _relative_module(path: str, node: ast.ImportFrom) -> str | None:
    """``from ._scheduler import scheduled`` を、file の在る package の名で読む(doeff_core_effects の中だけ)。"""
    if node.level == 0:
        return node.module
    parts = Path(path).parent.parts
    if node.level != 1 or not parts or parts[-1] != "doeff_core_effects":
        return None
    return "doeff_core_effects" if node.module is None else f"doeff_core_effects.{node.module}"


def python_names(tree: ast.AST, path: str) -> PythonNames:
    """file の import が doeff の ``scheduled`` に付けた名と、``scheduled`` を持つ module の別名。"""
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    froms = [
        (_relative_module(path, node), alias)
        for node in imports
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    plain = [alias for node in imports if isinstance(node, ast.Import) for alias in node.names]
    names = {
        alias.asname or NAME
        for module, alias in froms
        if module in SCHEDULER_MODULES and alias.name == NAME
    }
    modules = {
        alias.asname or alias.name
        for module, alias in froms
        if f"{module}.{alias.name}" in SCHEDULER_MODULES
    } | {
        alias.asname or alias.name if alias.name in SCHEDULER_MODULES else "doeff_core_effects"
        for alias in plain
        if alias.name in SCHEDULER_MODULES
        or (alias.asname is None and alias.name.split(".")[0] == "doeff_core_effects")
    }
    return PythonNames(frozenset(names), frozenset(modules))


def _rebinding(node: ast.AST, names: frozenset[str]) -> str | None:
    """node が doeff の ``scheduled`` の名を定義し直すなら、その訳。"""
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


class _PythonSource:
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


def _python_call(source: _PythonSource, call: ast.Call) -> Site:
    """``scheduled(...)`` の呼び 1 つの形と書き換え。"""
    line = call.lineno
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
        k.arg is None for k in call.keywords
    ):
        return Site(MANUAL, line, reason="*args か **kw で渡す")
    unknown = [k.arg for k in call.keywords if k.arg not in ("implementation", "body_program")]
    if unknown:
        return Site(MANUAL, line, reason=f"知らない keyword {unknown}")
    by_keyword = [k for k in call.keywords if k.arg == "body_program"]
    options = [k for k in call.keywords if k.arg == "implementation"]
    programs = [*call.args, *(k.value for k in by_keyword)]
    if not programs:
        return Site(NEW_FORM, line)
    if len(programs) > 1:
        return Site(MANUAL, line, reason="program を 2 つ以上渡す")
    if not options and not by_keyword:
        # scheduled(p) -> scheduled()(p): 字を足すだけなので、program の書いた形(改行・comment)はそのまま。
        func_end = source.end(call.func)
        return Site(POSITIONAL, line, (Edit(func_end, func_end, "()"),))
    option_text = ", ".join(source.segment(k) for k in options)
    rebuilt = f"{source.segment(call.func)}({option_text})({source.segment(programs[0])})"
    form = KEYWORD_PROGRAM if by_keyword else WITH_IMPLEMENTATION
    return Site(form, line, (Edit(source.start(call), source.end(call), rebuilt),))


def _python_partial(source: _PythonSource, call: ast.Call, names: PythonNames) -> Site | None:
    """``partial(scheduled, implementation=i)`` の形と書き換え(partial の第 1 引数が scheduled の時)。"""
    if _dotted(call.func) not in ("partial", "functools.partial") or not call.args:
        return None
    if not names.matches(call.args[0]):
        return None
    if len(call.args) > 1 or any(k.arg != "implementation" for k in call.keywords):
        return Site(MANUAL, call.lineno, reason="partial に program か知らない keyword を渡す")
    options = ", ".join(source.segment(k) for k in call.keywords)
    rebuilt = f"{source.segment(call.args[0])}({options})"
    return Site(PARTIAL, call.lineno, (Edit(source.start(call), source.end(call), rebuilt),))


def _python_node_sites(
    source: _PythonSource, node: ast.AST, names: PythonNames
) -> tuple[Site, ...]:
    """node 1 つが読む所(呼び・partial・属性)。名が値として出る所は ``python_sites`` が残りから拾う。"""
    if isinstance(node, ast.Call):
        partial = _python_partial(source, node, names)
        if partial is not None:
            return (partial,)
        if names.matches(node.func):
            return (_python_call(source, node),)
    if isinstance(node, ast.Attribute) and names.matches(node.value):
        return (Site(ATTRIBUTE, node.lineno),)
    return ()


def _claimed(node: ast.AST, names: PythonNames) -> tuple[ast.AST, ...]:
    """node の呼び・partial・属性が読み終えた名の式(値として数え直さない)。"""
    if isinstance(node, ast.Call):
        if (
            _dotted(node.func) in ("partial", "functools.partial")
            and node.args
            and names.matches(node.args[0])
        ):
            return (node.args[0],)
        if names.matches(node.func):
            return (node.func,)
    if isinstance(node, ast.Attribute) and names.matches(node.value):
        return (node.value,)
    return ()


def python_sites(text: str, path: str, assumed: PythonNames | None = None) -> list[Site]:
    """Python の text の中の、doeff の ``scheduled`` を読む所(読めない text は ``手で直す`` 1 つ)。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [Site(MANUAL, error.lineno or 1, reason=f"Python として読めない: {error.msg}")]
    found = python_names(tree, path)
    if assumed is not None:
        found = PythonNames(found.names | assumed.names, found.modules | assumed.modules)
    if not found.names and not found.modules:
        return []
    source = _PythonSource(text)
    nodes = list(ast.walk(tree))
    claimed = {id(expr) for node in nodes for expr in _claimed(node, found)}
    sites = [site for node in nodes for site in _python_node_sites(source, node, found)] + [
        Site(VALUE, node.lineno)
        for node in nodes
        if isinstance(node, (ast.Name, ast.Attribute))
        and isinstance(node.ctx, ast.Load)
        and id(node) not in claimed
        and found.matches(node)
    ]
    rebound = next((r for node in nodes if (r := _rebinding(node, found.names)) is not None), None)
    if rebound is not None:
        return [Site(MANUAL, site.line, reason=rebound) for site in sites if site.form in OLD_FORMS]
    return sites


# --- Python の文字列に書いた Hy ---


def _string_body(source: _PythonSource, node: ast.Constant) -> int | None:
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


def _string_hy_sites(text: str, source: _PythonSource, node: ast.Constant) -> tuple[Site, ...]:
    """文字列の literal 1 つの中の Hy の所(file の行と字の位置で)。"""
    value = node.value
    if not isinstance(value, str) or NAME not in value or "(import" not in value:
        return ()
    try:
        inner = hy_sites(value)
    except HyUnreadableError:
        return ()
    head = _string_body(source, node)
    if head is None:
        reason = "escape の在る文字列の中の Hy"
        return tuple(
            Site(MANUAL, node.lineno, reason=reason) for site in inner if site.form in OLD_FORMS
        )
    return tuple(site.moved(_line_of(text, head) - 1, head) for site in inner)


def embedded_hy_sites(text: str) -> list[Site]:
    """Python の文字列の literal に書いた Hy(test の中の Hy の text)の中の所。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    source = _PythonSource(text)
    return [
        site
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        for site in _string_hy_sites(text, source, node)
    ]


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


def _walk(nodes: Iterable[HyNode]) -> Iterator[HyNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _hy_head(node: HyNode) -> str | None:
    """``(head …)`` の head の atom(括弧の形でなければ None)。"""
    if node.kind != "form" or node.opener != "(" or not node.children:
        return None
    first = node.children[0]
    return first.text if first.kind == "atom" else None


def _hy_listed(names: HyNode) -> Iterator[str]:
    """``[a b :as c]`` の名の list から、doeff の ``scheduled`` に付ける atom。"""
    children = names.children
    for index, child in enumerate(children):
        if child.kind != "atom" or child.text != NAME:
            continue
        aliased = index + 2 < len(children) and children[index + 1].text == ":as"
        yield children[index + 2].text if aliased else NAME


def _hy_import_names(form: HyNode) -> Iterator[str]:
    """``(import m1 [names] m2 :as x m3 …)`` が doeff の ``scheduled`` に付ける atom。"""
    items = form.children[1:]
    index = 0
    while index < len(items):
        module = items[index]
        following = items[index + 1] if index + 1 < len(items) else None
        ours = module.kind == "atom" and module.text.replace("-", "_") in SCHEDULER_MODULES
        if following is not None and following.kind == "form" and following.opener == "[":
            if ours:
                yield from _hy_listed(following)
            index += 2
        elif following is not None and following.text == ":as" and index + 2 < len(items):
            if ours:
                yield f"{items[index + 2].text}.{NAME}"
            index += 3
        else:
            if ours:
                yield f"{module.text}.{NAME}"
            index += 1


def hy_names(nodes: Sequence[HyNode]) -> frozenset[str]:
    """Hy の text の import が doeff の ``scheduled`` に付ける atom(``scheduled``・別名・``m.scheduled``)。"""
    return frozenset(
        name
        for node in _walk(nodes)
        if _hy_head(node) == "import"
        for name in _hy_import_names(node)
    )


#: 名を定義する形の head(名・引数の list の名が doeff の ``scheduled`` の atom なら、その file は手で直す)。
_HY_DEFINERS = frozenset({"defn", "defn/a", "defk", "deff", "defp", "defmacro"})
_HY_THREADING = frozenset({"->", "->>"})


def _bound_names(node: HyNode) -> list[str]:
    """形 1 つが名に使う atom(``(setv a 1 b 2)``・``(fn [a] …)``・``(let [a 1] …)``・``(defn f [a] …)``)。"""
    head = _hy_head(node)
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


def _hy_rebound(nodes: Sequence[HyNode], atoms: frozenset[str]) -> str | None:
    """doeff の ``scheduled`` の atom を名に使う所の訳(在れば)。"""
    return next(
        (
            f"({_hy_head(node)} …) が {name} を名に使う"
            for node in _walk(nodes)
            for name in _bound_names(node)
            if name in atoms
        ),
        None,
    )


@dataclass(frozen=True)
class _HyArgs:
    """``(scheduled …)`` の引数の分け: 位置の引数・``:implementation`` の組・読めない訳。"""

    positional: tuple[HyNode, ...] = ()
    options: tuple[tuple[HyNode, HyNode], ...] = ()
    refused: str = ""


def _hy_args(args: Sequence[HyNode]) -> _HyArgs:
    """``(scheduled …)`` の引数を、位置の引数と ``:implementation`` の組に分ける。"""
    if any(arg.kind == "atom" and arg.text.startswith("#*") for arg in args):
        return _HyArgs(refused="#* か #** で渡す")
    keys = [
        i
        for i, arg in enumerate(args)
        if arg.kind == "atom" and arg.text.startswith(":") and len(arg.text) > 1
    ]
    values = {i + 1 for i in keys}
    if any(i + 1 >= len(args) for i in keys):
        return _HyArgs(refused="keyword に値が無い")
    unknown = [args[i].text for i in keys if args[i].text[1:].replace("-", "_") != "implementation"]
    if unknown:
        return _HyArgs(refused=f"知らない keyword {unknown}")
    return _HyArgs(
        positional=tuple(arg for i, arg in enumerate(args) if i not in values and i not in keys),
        options=tuple((args[i], args[i + 1]) for i in keys),
    )


def _hy_call(text: str, node: HyNode, threaded: bool) -> Site:
    """``(scheduled …)`` の形と書き換え(行は呼び手が字の位置から付ける)。"""
    head, *args = node.children
    if threaded:
        # (-> p (scheduled :implementation i)) は (scheduled p :implementation i) に開く: 段ごと工場の呼びの中へ入れる。
        return Site(THREADED, 0, (Edit(node.start, node.start, "("), Edit(node.end, node.end, ")")))
    split = _hy_args(args)
    if split.refused:
        return Site(MANUAL, 0, reason=split.refused)
    if not split.positional:
        return Site(NEW_FORM, 0)
    if len(split.positional) > 1:
        return Site(MANUAL, 0, reason="program を 2 つ以上渡す")
    option_text = " ".join(
        f"{key.text} {text[value.start : value.end]}" for key, value in split.options
    )
    factory = f"({head.text} {option_text})" if split.options else f"({head.text})"
    # 移す keyword は、前の形の終わりから値の終わりまで(前の空白ごと)消す。
    previous_end = {
        id(after): before.end for before, after in zip([head, *args], args, strict=False)
    }
    moved = tuple(Edit(previous_end[id(key)], value.end, "") for key, value in split.options)
    form = WITH_IMPLEMENTATION if split.options else POSITIONAL
    return Site(form, 0, (Edit(head.start, head.end, factory), *moved))


def _hy_visit(
    text: str, nodes: Sequence[HyNode], atoms: frozenset[str], parent: HyNode | None
) -> Iterator[tuple[int, Site]]:
    """形の木を歩き、doeff の ``scheduled`` の atom ごとに (字の位置, 所) を出す。import の形の中は見ない。"""
    attributes = tuple(f"{atom}." for atom in atoms)
    in_threading = parent is not None and _hy_head(parent) in _HY_THREADING
    for index, node in enumerate(nodes):
        threaded = in_threading and index >= 2
        if node.kind == "atom" and node.text in atoms:
            # (-> p scheduled) は (scheduled p) に開く: 工場の呼びの段 ((scheduled)) にする。
            edits = (Edit(node.start, node.end, f"(({node.text}))"),) if threaded else ()
            yield node.start, Site(THREADED if threaded else VALUE, 0, edits)
        elif node.kind == "atom" and node.text.startswith(attributes):
            yield node.start, Site(ATTRIBUTE, 0)
        elif node.kind == "form" and _hy_head(node) != "import":
            if _hy_head(node) in atoms:
                yield node.start, _hy_call(text, node, threaded)
                yield from _hy_visit(text, node.children[1:], atoms, node)
            else:
                yield from _hy_visit(text, node.children, atoms, node)


def hy_sites(text: str, assumed: frozenset[str] = frozenset()) -> list[Site]:
    """Hy の text の中の、doeff の ``scheduled`` を読む所(行は text の行)。"""
    nodes = parse_hy(text)
    atoms = hy_names(nodes) | assumed
    if not atoms:
        return []
    sites = [
        Site(site.form, _line_of(text, offset), site.edits, site.reason)
        for offset, site in _hy_visit(text, nodes, atoms, None)
    ]
    rebound = _hy_rebound(nodes, atoms)
    if rebound is not None:
        return [Site(MANUAL, site.line, reason=rebound) for site in sites if site.form in OLD_FORMS]
    return sites


# --- markdown ---

_FENCE = re.compile(r"^[ \t]*(?P<fence>```+|~~~+)[ \t]*(?P<lang>[\w+-]*)[^\n]*\n", re.MULTILINE)


def _fences(text: str) -> Iterator[tuple[str, int, int]]:
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


def _fence_sites(
    text: str, path: str, lang: str, start: int, end: int
) -> tuple[tuple[Language, Site], ...]:
    """囲み 1 つの中の所(file の行と字の位置で)。断片の例(``...`` や出力の混ざった囲み)は数えない。"""
    body = text[start:end]
    if NAME not in body:
        return ()
    assumed = "doeff_core_effects" in text or "doeff-core-effects" in text
    language: Language
    if lang in _PYTHON_FENCES:
        language = "markdown の Python"
        names = PythonNames(frozenset({NAME}) if assumed else frozenset(), frozenset())
        inner = python_sites(body, path, names)
    elif lang in _HY_FENCES:
        language = "markdown の Hy"
        try:
            inner = hy_sites(body, frozenset({NAME}) if assumed else frozenset())
        except HyUnreadableError:
            return ()
    else:
        return ()
    lines = _line_of(text, start) - 1
    return tuple(
        (language, site.moved(lines, start))
        for site in inner
        if not site.reason.startswith("Python として読めない")
    )


def markdown_sites(text: str, path: str) -> list[tuple[Language, Site]]:
    """markdown の code の囲みの中の所(Python と Hy)。"""
    return [
        found
        for lang, start, end in _fences(text)
        for found in _fence_sites(text, path, lang, start, end)
    ]


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


def read_file(path: str, text: str) -> list[tuple[Language, Site]]:
    """file 1 つの中の所(言語つき)。"""
    if NAME not in text:
        return []
    if path.endswith(PYTHON_SUFFIXES):
        python: list[tuple[Language, Site]] = [("Python", s) for s in python_sites(text, path)]
        return python + [("Python の文字列の Hy", s) for s in embedded_hy_sites(text)]
    if path.endswith(HY_SUFFIXES):
        try:
            return [("Hy", s) for s in hy_sites(text)]
        except HyUnreadableError as error:
            return [("Hy", Site(MANUAL, 1, reason=f"Hy として読めない: {error}"))]
    return markdown_sites(text, path)


def rewrite_text(path: str, text: str, max_rounds: int = 8) -> tuple[str, Counter[str]]:
    """file 1 つを書き換えた text と、書き換えた形ごとの数。重なる所は外側を先に当て、読み直して繰り返す。"""
    done: Counter[str] = Counter()
    for _ in range(max_rounds):
        chosen = _disjoint(site for _, site in read_file(path, text))
        if not chosen:
            break
        done.update(site.form for site in chosen)
        text = _apply(text, (edit for site in chosen for edit in site.edits))
    return text, done


def _git(tree: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *args], check=True, capture_output=True, text=True, input=stdin
    ).stdout


def _wanted(path: str, prefixes: Sequence[str]) -> bool:
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


def census(tree: Path, prefixes: Sequence[str], rev: str | None = None) -> list[FileReport]:
    """木の file ごとの所(所の無い file は出さない)。"""
    reports = (
        FileReport(path, is_history(path), KEEP_MARK in text, tuple(read_file(path, text)))
        for path, text in tree_texts(tree, prefixes, rev)
    )
    return [report for report in reports if report.sites]


def summary(reports: Sequence[FileReport]) -> dict[str, object]:
    """区画・言語・形ごとの数(歴史の記録と残す印の file は別に数える)と、手で直す所と値として渡す所の場所。"""
    live = [
        (report, language, site)
        for report in reports
        if not report.left
        for language, site in report.sites
    ]
    by_section: dict[str, Counter[str]] = {}
    for report, _, site in live:
        by_section.setdefault(section_of(report.path), Counter())[site.form] += 1
    work_files = Counter(
        section_of(path)
        for path in {r.path for r, _, s in live if s.form in OLD_FORMS or s.form == MANUAL}
    )
    return {
        "sections": {
            section: {
                "forms": dict(counts),
                "old": sum(counts[f] for f in OLD_FORMS) + counts[MANUAL],
                "files": work_files[section],
            }
            for section, counts in sorted(by_section.items())
        },
        "languages": dict(
            sorted(Counter(f"{lang} / {site.form}" for _, lang, site in live).items())
        ),
        "history": dict(
            Counter(site.form for report in reports if report.history for _, site in report.sites)
        ),
        "kept": {report.path: len(report.sites) for report in reports if report.kept},
        "manual": [
            {"path": r.path, "line": s.line, "reason": s.reason}
            for r, _, s in live
            if s.form == MANUAL
        ],
        "values": [{"path": r.path, "line": s.line} for r, _, s in live if s.form == VALUE],
    }


def _print_summary(
    found: Mapping[str, object], reports: Sequence[FileReport], args: argparse.Namespace
) -> None:
    sections = found["sections"]
    assert isinstance(sections, dict)
    total: Counter[str] = reduce(
        lambda acc, row: acc + Counter(row["forms"]), sections.values(), Counter()
    )
    print("区画\t書き換える所(旧い形と手で直す所)\tfile\t" + "\t".join(FORMS))
    for section, row in sections.items():
        print(
            f"{section}\t{row['old']}\t{row['files']}\t"
            + "\t".join(str(row["forms"].get(f, 0)) for f in FORMS)
        )
    old_total = sum(total[f] for f in OLD_FORMS) + total[MANUAL]
    file_total = sum(row["files"] for row in sections.values())
    print(f"合計\t{old_total}\t{file_total}\t" + "\t".join(str(total[f]) for f in FORMS))
    languages = found["languages"]
    assert isinstance(languages, dict)
    print("\n言語 / 形\t数")
    print("\n".join(f"{key}\t{count}" for key, count in languages.items()))
    print(f"\n歴史の記録(書き換えない): {found['history']}")
    print(f"残す印の file(書き換えない・所の数): {found['kept']}")
    manual = found["manual"]
    assert isinstance(manual, list)
    if manual:
        print("\n手で直す所:")
        print("\n".join(f"  {m['path']}:{m['line']}: {m['reason']}" for m in manual))
    values = found["values"]
    assert isinstance(values, list)
    if args.values and values:
        print("\n値として渡す所(書き換えない):")
        print("\n".join(f"  {v['path']}:{v['line']}" for v in values))
    if args.files:
        print("\nfile ごと:")
        for report in reports:
            mark = "(歴史)" if report.history else "(残す印)" if report.kept else ""
            print(f"  {report.path}{mark}: {dict(Counter(site.form for _, site in report.sites))}")


def _write_json(target: str | None, data: object) -> None:
    if target is None:
        return
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if target == "-":
        print(text)
    else:
        Path(target).write_text(text + "\n", encoding="utf-8")


def _rewrite(tree: Path, prefixes: Sequence[str], dry_run: bool) -> dict[str, object]:
    """作業木の file を書き換え、file ごとの書き換えた形の数と、残った手で直す所を返す。"""
    changed = [
        (report.path, before, *rewrite_text(report.path, before))
        for report in census(tree, prefixes)
        if not report.left
        for before in ((tree / report.path).read_text(encoding="utf-8"),)
    ]
    rewritten = {path: dict(done) for path, before, after, done in changed if after != before}
    if not dry_run:
        for path, before, after, _ in changed:
            if after != before:
                (tree / path).write_text(after, encoding="utf-8")
    left = summary(census(tree, prefixes))
    return {"rewritten": rewritten, "manual": left["manual"], "values": left["values"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("census", "rewrite"):
        sub = commands.add_parser(command)
        sub.add_argument("--tree", type=Path, default=Path.cwd())
        sub.add_argument("--path", action="append", default=[])
        sub.add_argument("--json")
        if command == "census":
            sub.add_argument("--rev")
            sub.add_argument("--files", action="store_true")
            sub.add_argument("--values", action="store_true")
        else:
            sub.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    tree = args.tree.resolve()
    if args.command == "census":
        reports = census(tree, args.path, args.rev)
        found = summary(reports)
        _print_summary(found, reports, args)
        _write_json(args.json, found)
        return 0
    result = _rewrite(tree, args.path, args.dry_run)
    rewritten = result["rewritten"]
    assert isinstance(rewritten, dict)
    total = reduce(lambda acc, counts: acc + Counter(counts), rewritten.values(), Counter())
    print(
        f"{'書き換える' if args.dry_run else '書き換えた'} file: {len(rewritten)}・所: {dict(total)}"
    )
    manual = result["manual"]
    assert isinstance(manual, list)
    print("\n".join(f"  残り(手で直す): {m['path']}:{m['line']}: {m['reason']}" for m in manual))
    _write_json(args.json, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
