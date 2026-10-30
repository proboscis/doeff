"""引数の無い factory から素の handler になった名の、旧い呼び ``name()``・``(name)`` を名そのものへ書き換える道具。

決まり: ADR-DOE-API-002(decision 2026-10-29)— 設定も呼びごとの状態も無い
handler は値で、``sequential(prog)``・``[sequential, …]`` と書く。旧い ``sequential()`` は 1 つの版の間 DeprecationWarning
つきで同じ handler を返す(``doeff.program._former_factory_handler``)。その版の間に呼び手をこの道具で書き換える
(agora card ki-b10e81c19717)。

使い方(doeff の作業木で)::

    uv run python tools/former_factory_call_rewrite.py census --name sequential --tree ~/repos/agora-controllers --rev origin/main
    uv run python tools/former_factory_call_rewrite.py rewrite --name sequential --tree .

- ``--name``(何度でも・既定は表 ``FORMER_FACTORIES`` の全部): 書き換える名。
- ``census``: 形ごとの数を区画ごとに出す。``--rev`` を渡すと作業木でなく git の版の中身を読む。``--files`` で file ごとの数も出す。
- ``rewrite``: 作業木を書き換える。書き換えない所(``手で直す``)は場所と訳を出して残す。``--dry-run`` は書かずに数だけ出す。
- ``--path``(何度でも): 木からの相対の頭で、見る file を絞る。``--json <file>``(``-`` は stdout): 結果を JSON で書く。

見る file・歴史の記録(書き換えない)・Hy の読み・markdown の囲みは tools/call_site_reading.py の部品を借りる。
印 ``former-factory-call-rewrite: keep`` を書いた file(この道具の test — 旧い形をわざと書いた入力)は書き換えない。

どの名が対象か: file の import が読む名(``from doeff_traverse import sequential as s``・``(import doeff-traverse [sequential])``・
module の別名の ``m.sequential``)。Hy の atom は ``-`` を ``_`` と同じに読む(``await-handler``)。名を定義し直す file は
旧い呼びを全部 ``手で直す`` に数える。markdown の囲みは、file が名の package を書いていれば名を対象と読む。

形(census の鍵)と書き換え:

- 旧い呼び: ``sequential()`` -> ``sequential``・``(sequential)`` -> ``sequential``(``((sequential) p)`` は ``(sequential p)``・
  ``(-> p (sequential))`` は ``(-> p sequential)`` になり、どちらも同じ意味)
- 新しい呼び: ``sequential(p)``・``(sequential p)``(書き換えない)
- 値: 呼ばずに名を読む所(``[sequential, …]``・``(-> p sequential)``・属性 ``sequential.__doc__``)(書き換えない)
- 手で直す: 引数が keyword か ``*args``・名を定義し直す file・読めない file・escape の在る文字列の中の Hy
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
import types
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


def _borrowed() -> types.ModuleType:
    """tools/call_site_reading.py(tools/ は package ではない)— Hy の読み・字の範囲・木の読みを借りる。"""
    loaded = sys.modules.get("call_site_reading")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "call_site_reading", Path(__file__).with_name("call_site_reading.py")
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["call_site_reading"] = module
    spec.loader.exec_module(module)
    return module


_reading = _borrowed()
Edit = _reading.Edit
Site = _reading.Site
HyNode = _reading.HyNode
HyUnreadableError = _reading.HyUnreadableError

#: 素の handler になった名と、それを出す module(移し替えの module も)。ADR-DOE-API-002 R6 の card で 1 つずつ足す。
FORMER_FACTORIES: Mapping[str, frozenset[str]] = {
    "sequential": frozenset(
        {"doeff_traverse", "doeff_traverse.handlers", "doeff_traverse._handlers"}
    ),
    "await_handler": frozenset(
        {"doeff_core_effects", "doeff_core_effects.handlers", "doeff_core_effects._handlers"}
    ),
    # doeff-gemini・doeff-seedream などの production_handlers は設定を取る factory のまま — 3 つの package の import だけを読む。
    "production_handlers": frozenset(
        {
            f"{package}{module}"
            for package in ("doeff_git", "doeff_openai", "doeff_openrouter")
            for module in ("", ".handlers", ".handlers.production", "._handlers", "._handlers.production")
        }
    ),
    "agent_handler": frozenset(
        {
            "doeff_conductor",
            "doeff_conductor.handlers",
            "doeff_conductor._handlers",
            "doeff_conductor._handlers.agent_handler",
        }
    ),
    "git_handler": frozenset(
        {
            "doeff_conductor",
            "doeff_conductor.handlers",
            "doeff_conductor._handlers",
            "doeff_conductor._handlers.git_handler",
        }
    ),
}

#: 旧い形をわざと書いた file の印(この道具の test が持つ)。
KEEP_MARK = "former-factory-call-rewrite: keep"

OLD_CALL = "旧い呼び"
NEW_CALL = "新しい呼び"
VALUE = "値"
MANUAL = "手で直す"
FORMS = (OLD_CALL, MANUAL, NEW_CALL, VALUE)


@dataclass(frozen=True)
class Target:
    """書き換える名 1 つ: Python の名と、それを出す module。"""

    name: str
    modules: frozenset[str]

    @property
    def package(self) -> str:
        """markdown の囲みが名を対象と読む印の package(``doeff_traverse``)。"""
        return min(self.modules, key=len)


def targets(names: Sequence[str]) -> tuple[Target, ...]:
    chosen = names or tuple(FORMER_FACTORIES)
    unknown = [n for n in chosen if n not in FORMER_FACTORIES]
    if unknown:
        raise SystemExit(f"表 FORMER_FACTORIES に無い名: {unknown}")
    return tuple(Target(n, FORMER_FACTORIES[n]) for n in chosen)


@dataclass(frozen=True)
class FileReport:
    path: str
    history: bool
    kept: bool
    sites: tuple[tuple[str, Site], ...]

    @property
    def left(self) -> bool:
        return self.history or self.kept


# --- Python ---


@dataclass(frozen=True)
class PythonNames:
    """Python の file で対象を指す名(名か別名)と、名を持つ module の別名。"""

    target: Target
    names: frozenset[str]
    modules: frozenset[str]

    def matches(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.names
        return (
            isinstance(node, ast.Attribute)
            and node.attr == self.target.name
            and _reading.dotted(node.value) in self.modules
        )


def python_names(tree: ast.AST, target: Target) -> PythonNames:
    """file の import が対象に付けた名と、対象を持つ module の別名(相対の import は読まない — 定義する package の中だけ)。"""
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    froms = [
        (node.module, alias)
        for node in imports
        if isinstance(node, ast.ImportFrom) and node.level == 0
        for alias in node.names
    ]
    plain = [alias for node in imports if isinstance(node, ast.Import) for alias in node.names]
    names = {
        alias.asname or target.name
        for module, alias in froms
        if module in target.modules and alias.name == target.name
    }
    modules = {
        alias.asname or alias.name
        for module, alias in froms
        if f"{module}.{alias.name}" in target.modules
    } | {alias.asname or alias.name for alias in plain if alias.name in target.modules}
    return PythonNames(target, frozenset(names), frozenset(modules))


def _python_rebound(nodes: Sequence[ast.AST], names: frozenset[str]) -> str | None:
    return next((r for node in nodes if (r := _reading.rebinding(node, names)) is not None), None)


def python_sites(text: str, target: Target, assumed: bool = False) -> list[Site]:
    """Python の text の中の、対象を読む所(読めない text は ``手で直す`` 1 つ)。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [Site(MANUAL, error.lineno or 1, reason=f"Python として読めない: {error.msg}")]
    found = python_names(tree, target)
    if assumed:
        found = PythonNames(target, found.names | {target.name}, found.modules)
    if not found.names and not found.modules:
        return []
    source = _reading.PythonSource(text)
    nodes = list(ast.walk(tree))
    called = {id(node.func): node for node in nodes if isinstance(node, ast.Call)}
    sites: list[Site] = []
    for node in nodes:
        if not isinstance(node, (ast.Name, ast.Attribute)) or not found.matches(node):
            continue
        if not isinstance(node.ctx, ast.Load):
            continue
        call = called.get(id(node))
        if call is None:
            sites.append(Site(VALUE, node.lineno))
        elif call.keywords or any(isinstance(arg, ast.Starred) for arg in call.args):
            sites.append(Site(MANUAL, call.lineno, reason="keyword か *args で渡す"))
        elif call.args:
            sites.append(Site(NEW_CALL, call.lineno))
        else:
            # name() -> name: 呼びの括弧を消す(名の書いた形 m.sequential はそのまま)。
            sites.append(
                Site(OLD_CALL, call.lineno, (Edit(source.end(node), source.end(call), ""),))
            )
    rebound = _python_rebound(nodes, found.names)
    if rebound is not None:
        return [Site(MANUAL, s.line, reason=rebound) for s in sites if s.form == OLD_CALL]
    return sites


def embedded_hy_sites(text: str, target: Target) -> list[Site]:
    """Python の文字列の literal に書いた Hy(test の中の Hy の text)の中の所。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    source = _reading.PythonSource(text)
    found: list[Site] = []
    for node in ast.walk(tree):
        value = node.value if isinstance(node, ast.Constant) else None
        if not isinstance(value, str) or "(import" not in value or not _mentions(value, target):
            continue
        try:
            inner = hy_sites(value, target)
        except HyUnreadableError:
            continue
        head = _reading.string_body(source, node)
        if head is None:
            reason = "escape の在る文字列の中の Hy"
            found += [Site(MANUAL, node.lineno, reason=reason) for s in inner if s.form == OLD_CALL]
        else:
            found += [s.moved(_reading.line_of(text, head) - 1, head) for s in inner]
    return found


# --- Hy ---


def _mangled(atom: str) -> str:
    return atom.replace("-", "_")


def _mentions(text: str, target: Target) -> bool:
    return target.name in text or target.name.replace("_", "-") in text


def _hy_import_names(form: HyNode, target: Target) -> Iterator[str]:
    """``(import m1 [names] m2 :as x m3 …)`` が対象に付ける atom(名・別名・``m.名``)。"""
    items = form.children[1:]
    index = 0
    while index < len(items):
        module = items[index]
        following = items[index + 1] if index + 1 < len(items) else None
        ours = module.kind == "atom" and _mangled(module.text) in target.modules
        if following is not None and following.kind == "form" and following.opener == "[":
            if ours:
                children = following.children
                for i, child in enumerate(children):
                    if child.kind == "atom" and _mangled(child.text) == target.name:
                        aliased = i + 2 < len(children) and children[i + 1].text == ":as"
                        yield children[i + 2].text if aliased else child.text
            index += 2
        elif following is not None and following.text == ":as" and index + 2 < len(items):
            if ours:
                yield f"{items[index + 2].text}.{target.name}"
            index += 3
        else:
            if ours:
                yield f"{module.text}.{target.name}"
            index += 1


def _hy_visit(nodes: Sequence[HyNode], atoms: frozenset[str]) -> Iterator[tuple[int, Site]]:
    """形の木を歩き、対象の atom ごとに (字の位置, 所) を出す。import の形の中は見ない。"""
    for node in nodes:
        if node.kind == "atom" and _mangled(node.text) in atoms:
            yield node.start, Site(VALUE, 0)
        elif node.kind == "form" and _reading.hy_head(node) != "import":
            head = _reading.hy_head(node)
            if head is not None and _mangled(head) in atoms:
                name, *args = node.children
                if not args:
                    # (name) -> name
                    yield node.start, Site(OLD_CALL, 0, (Edit(node.start, node.end, name.text),))
                elif any(a.kind == "atom" and (a.text.startswith((":", "#*"))) for a in args):
                    yield node.start, Site(MANUAL, 0, reason="keyword か #* で渡す")
                else:
                    yield node.start, Site(NEW_CALL, 0)
                yield from _hy_visit(node.children[1:], atoms)
            else:
                yield from _hy_visit(node.children, atoms)


def hy_sites(text: str, target: Target, assumed: bool = False) -> list[Site]:
    """Hy の text の中の、対象を読む所(行は text の行)。"""
    nodes = _reading.parse_hy(text)
    imported = {
        name
        for node in _reading.walk(nodes)
        if _reading.hy_head(node) == "import"
        for name in _hy_import_names(node, target)
    }
    atoms = frozenset(_mangled(a) for a in imported) | (
        frozenset({target.name}) if assumed else frozenset()
    )
    if not atoms:
        return []
    sites = [
        Site(s.form, _reading.line_of(text, offset), s.edits, s.reason)
        for offset, s in _hy_visit(nodes, atoms)
    ]
    rebound = next(
        (
            f"({_reading.hy_head(node)} …) が {name} を名に使う"
            for node in _reading.walk(nodes)
            for name in _reading.bound_names(node)
            if _mangled(name) in atoms
        ),
        None,
    )
    if rebound is not None:
        return [Site(MANUAL, s.line, reason=rebound) for s in sites if s.form == OLD_CALL]
    return sites


# --- markdown ---


def markdown_sites(text: str, target: Target) -> list[tuple[str, Site]]:
    """markdown の code の囲みの中の所(Python と Hy)。断片の例(読めない囲み)は数えない。"""
    assumed = target.package in text or target.package.replace("_", "-") in text
    found: list[tuple[str, Site]] = []
    for lang, start, end in _reading.fences(text):
        body = text[start:end]
        if not _mentions(body, target):
            continue
        if lang in _reading.PYTHON_FENCES:
            language = "markdown の Python"
            inner = [
                s
                for s in python_sites(body, target, assumed)
                if not s.reason.startswith("Python として読めない")
            ]
        elif lang in _reading.HY_FENCES:
            language = "markdown の Hy"
            try:
                inner = hy_sites(body, target, assumed)
            except HyUnreadableError:
                continue
        else:
            continue
        lines = _reading.line_of(text, start) - 1
        found += [(language, s.moved(lines, start)) for s in inner]
    return found


# --- 木 ---


def read_file(path: str, text: str, target: Target) -> list[tuple[str, Site]]:
    """file 1 つの中の対象の所(言語つき)。"""
    if not _mentions(text, target):
        return []
    if path.endswith(_reading.PYTHON_SUFFIXES):
        python = [("Python", s) for s in python_sites(text, target)]
        return python + [("Python の文字列の Hy", s) for s in embedded_hy_sites(text, target)]
    if path.endswith(_reading.HY_SUFFIXES):
        try:
            return [("Hy", s) for s in hy_sites(text, target)]
        except HyUnreadableError as error:
            return [("Hy", Site(MANUAL, 1, reason=f"Hy として読めない: {error}"))]
    return markdown_sites(text, target)


def rewrite_text(path: str, text: str, target: Target, max_rounds: int = 8) -> tuple[str, int]:
    """file 1 つを書き換えた text と、書き換えた所の数。重なる所は外側を先に当て、読み直して繰り返す。"""
    done = 0
    for _ in range(max_rounds):
        chosen = _reading.disjoint(s for _, s in read_file(path, text, target))
        if not chosen:
            break
        done += len(chosen)
        text = _reading.apply_edits(text, (edit for s in chosen for edit in s.edits))
    return text, done


def census(
    tree: Path, prefixes: Sequence[str], target: Target, rev: str | None = None
) -> list[FileReport]:
    reports = (
        FileReport(
            path, _reading.is_history(path), KEEP_MARK in text, tuple(read_file(path, text, target))
        )
        for path, text in _reading.tree_texts(tree, prefixes, rev)
    )
    return [r for r in reports if r.sites]


def summary(reports: Sequence[FileReport]) -> dict[str, object]:
    """区画・形ごとの数(歴史の記録と残す印の file は別に数える)と、手で直す所の場所。"""
    live = [(r, lang, s) for r in reports if not r.left for lang, s in r.sites]
    by_section: dict[str, Counter[str]] = {}
    for report, _, site in live:
        by_section.setdefault(_reading.section_of(report.path), Counter())[site.form] += 1
    return {
        "sections": {section: dict(counts) for section, counts in sorted(by_section.items())},
        "languages": dict(sorted(Counter(f"{lang} / {s.form}" for _, lang, s in live).items())),
        "old": sum(1 for _, _, s in live if s.form in (OLD_CALL, MANUAL)),
        "history": dict(Counter(s.form for r in reports if r.history for _, s in r.sites)),
        "kept": {r.path: len(r.sites) for r in reports if r.kept},
        "manual": [
            {"path": r.path, "line": s.line, "reason": s.reason}
            for r, _, s in live
            if s.form == MANUAL
        ],
    }


def _print_summary(
    name: str, found: Mapping[str, object], reports: Sequence[FileReport], files: bool
) -> None:
    sections = found["sections"]
    assert isinstance(sections, dict)
    print(f"== {name}: 書き換える所(旧い呼びと手で直す所) {found['old']}")
    print("区画\t" + "\t".join(FORMS))
    for section, counts in sections.items():
        print(f"{section}\t" + "\t".join(str(counts.get(f, 0)) for f in FORMS))
    languages = found["languages"]
    assert isinstance(languages, dict)
    print("\n".join(f"  {key}\t{count}" for key, count in languages.items()))
    print(f"歴史の記録(書き換えない): {found['history']}")
    print(f"残す印の file(書き換えない・所の数): {found['kept']}")
    manual = found["manual"]
    assert isinstance(manual, list)
    print("\n".join(f"  手で直す: {m['path']}:{m['line']}: {m['reason']}" for m in manual))
    if files:
        for report in reports:
            mark = "(歴史)" if report.history else "(残す印)" if report.kept else ""
            print(f"  {report.path}{mark}: {dict(Counter(s.form for _, s in report.sites))}")


def _rewrite(
    tree: Path, prefixes: Sequence[str], target: Target, dry_run: bool
) -> dict[str, object]:
    """作業木の file を書き換え、file ごとの書き換えた所の数と、残った手で直す所を返す。"""
    rewritten: dict[str, int] = {}
    for report in census(tree, prefixes, target):
        if report.left:
            continue
        file = tree / report.path
        before = file.read_text(encoding="utf-8")
        after, done = rewrite_text(report.path, before, target)
        if after != before:
            rewritten[report.path] = done
            if not dry_run:
                file.write_text(after, encoding="utf-8")
    left = summary(census(tree, prefixes, target))
    return {"rewritten": rewritten, "manual": left["manual"]}


def _write_json(target: str | None, data: object) -> None:
    if target is None:
        return
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if target == "-":
        print(text)
    else:
        Path(target).write_text(text + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("census", "rewrite"):
        sub = commands.add_parser(command)
        sub.add_argument("--name", action="append", default=[])
        sub.add_argument("--tree", type=Path, default=Path.cwd())
        sub.add_argument("--path", action="append", default=[])
        sub.add_argument("--json")
        if command == "census":
            sub.add_argument("--rev")
            sub.add_argument("--files", action="store_true")
        else:
            sub.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    tree = args.tree.resolve()
    results: dict[str, object] = {}
    for target in targets(args.name):
        if args.command == "census":
            reports = census(tree, args.path, target, args.rev)
            found = summary(reports)
            _print_summary(target.name, found, reports, args.files)
            results[target.name] = found
            continue
        result = _rewrite(tree, args.path, target, args.dry_run)
        rewritten = result["rewritten"]
        assert isinstance(rewritten, dict)
        verb = "書き換える" if args.dry_run else "書き換えた"
        print(f"== {target.name}: {verb} file {len(rewritten)}・所 {sum(rewritten.values())}")
        manual = result["manual"]
        assert isinstance(manual, list)
        print(
            "\n".join(f"  残り(手で直す): {m['path']}:{m['line']}: {m['reason']}" for m in manual)
        )
        results[target.name] = result
    _write_json(args.json, results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
