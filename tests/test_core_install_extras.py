"""`pip install doeff` は核の 4 個だけを入れ、Hy と HTTP は extra で足す事の失敗ケース(取り組み doeff-publish の区切り 8・
card ki-006338b48f4b)。

核の 4 個 = doeff・doeff-vm・doeff-core-effects・frozendict。`import doeff` と README の Python の例は、この 4 個だけで動く
(依存の報告 2026-10-16-doeff-core-dependency-tree.html の ★ 案 D)。Hy の言語(doeff-hy・hy・pydantic …)と httpx は extra で足す:

- hy   = doeff-hy(Hy で書いた effect と handler・cache・CLI の doeff run が要る)
- http = doeff-hy と httpx(HTTP の client の handler は Hy で書いてあり、httpx で送る)
- all  = hy と http(0.5 までの既定と同じ組)

doeff の extra は doeff-core-effects の同じ名の extra を引く。doeff-core-effects の他の extra(http-server・postgres)も、答え手を
Hy で書いてあるので doeff-hy を引く。

workspace の部品が Hy で書いた doeff_core_effects の module(と、それを import の時に読む module)か doeff_hy・hy・httpx を import
するなら、その部品の既定の依存がそれを入れる — 入れないと、PyPI から入れた利用者の手元で `No module named 'doeff_hy'` で落ちる
(手元の開発は dev の group が両方を入れるので落ちない)。

依存は pyproject.toml の宣言から読む(本物の install で確かめるのは、区切り 8 の card 3 の CI の検)。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Iterator
from functools import cache
from itertools import accumulate, takewhile, tee
from pathlib import Path

import tomllib
from packaging.requirements import Requirement
from packaging.utils import NormalizedName, canonicalize_name

REPO = Path(__file__).resolve().parents[1]
CORE_EFFECTS_DIR = REPO / "packages" / "doeff-core-effects"
CORE_FOUR = frozenset(
    map(canonicalize_name, ["doeff", "doeff-vm", "doeff-core-effects", "frozendict"])
)
DOEFF_HY = canonicalize_name("doeff-hy")
HY = canonicalize_name("hy")
HTTPX = canonicalize_name("httpx")
NOTHING: frozenset[NormalizedName] = frozenset()
# import の頭の名 → それを入れる配布物。
EXTERNAL = {"doeff_hy": DOEFF_HY, "hy": HY, "httpx": HTTPX}
# 部品の dir のうち、配る source でない物(検・例・測り)。
NOT_SHIPPED = frozenset({"tests", "test", "examples", "benchmarks"})
# この検が走査しない部品と訳。
NOT_SCANNED = {
    # Hy の module の持ち主。自分の module の要る物は、下の _core_effects_needs が読む。
    canonicalize_name("doeff-core-effects"): "Hy の module の持ち主",
    # CLI の doeff run は Hy 無しで動き、--hy と .hy の program の時だけ遅い import で Hy を読む。Hy が無ければ、足す extra の名を
    # 言う文で落ちる(tests/cli/test_cli_without_hy.py・doeff_core_effects/_extra_hints.py)。
    canonicalize_name("doeff"): "CLI の --hy と .hy の program だけが Hy を読む",
    # doeff-hy の依存としてだけ入る pytest の plugin(doeff-hy が doeff-hy-pytest を引く — 逆に引くと輪になる)。
    canonicalize_name("doeff-hy-pytest"): "doeff-hy が引く plugin",
}
# 部品 → import の読みを満たす extra(既定の依存に足して読む)。
USED_WITH_EXTRA = {
    # doeff-core-effects を読むのは dogfood の宣言の module だけで、extra dogfood の時に限る(ADR-DOE-DOMAIN-001 D1)。
    canonicalize_name("doeff-domain"): "dogfood",
}
_OPEN = frozenset("([{")
_CLOSE = frozenset(")]}")
# Hy の source の文字列と註(左から先に始まる方が勝つ — 文字列の中の ; と、註の中の " を取り違えない)。
_HY_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|;[^\n]*')
_HY_TOKEN = re.compile(r"[\[\](){}]|[^\s\[\](){}]+")
# 部品の source が下の検の読む物に触れるかの目印(触れない file は解かない)。
_MENTIONS = re.compile(r"doeff[_-]core[_-]effects|doeff[_-]hy|httpx|\bhy\b")


@cache
def _projects() -> dict[NormalizedName, tuple[Path, dict[str, object]]]:
    """workspace の部品(根の doeff も)の名 → (部品の dir, pyproject.toml の表 project)。"""
    tables = (
        (pyproject.parent, tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"])
        for pyproject in [REPO / "pyproject.toml", *REPO.glob("packages/*/pyproject.toml")]
    )
    return {canonicalize_name(project["name"]): (where, project) for where, project in tables}


def _lines(project: dict[str, object], extra: str | None) -> tuple[str, ...]:
    """部品の既定の依存(extra が None)か、extra 1 つの依存の PEP 508 の文。"""
    match extra, project:
        case None, {"dependencies": list() as lines}:
            return tuple(map(str, lines))
        case None, _:
            return ()
        case str(), {"optional-dependencies": dict() as extras} if extra in extras:
            return tuple(map(str, extras[extra]))
        case _:
            raise AssertionError(f"{project['name']} に extra {extra!r} が無い(pip は黙って飛ばす)")


def _keys(requirements: Iterable[Requirement]) -> frozenset[tuple[NormalizedName, str | None]]:
    """依存の文 → (配布物の名, 辿る extra か既定の None) の組。"""
    return frozenset(
        (canonicalize_name(requirement.name), extra)
        for requirement in requirements
        for extra in (None, *requirement.extras)
    )


def _installs(requirements: Iterable[Requirement]) -> frozenset[NormalizedName]:
    """requirements を入れると入る物の名 — workspace の部品は既定の依存と頼んだ extra を辿り、外の物は辿らない。"""
    projects = _projects()
    keys = _keys(requirements)
    while (
        grown := keys
        | _keys(
            Requirement(line)
            for name, extra in keys
            if name in projects
            for line in _lines(projects[name][1], extra)
        )
    ) != keys:
        keys = grown
    return frozenset(name for name, _ in keys)


def _hy_code_only(text: str) -> str:
    """Hy の source の文字列と註を空白に替える(行は残す)— 中の括弧と import の語を拾わないため。"""
    return _HY_STRING_OR_COMMENT.sub(lambda found: re.sub(r"[^\n]", " ", found.group()), text)


def _hy_form(code: str, start: int) -> list[str]:
    """start で開く括弧の form 1 つの語の列(開きと閉じの括弧を含む)。"""
    # 語は form の閉じまでだけ読む(file の終わりまで切り出さない — import の form ごとに file を読み直すと遅い)。
    tokens, counted = tee(found.group() for found in _HY_TOKEN.finditer(code, start))
    depths = accumulate(1 if token in _OPEN else -1 if token in _CLOSE else 0 for token in counted)
    # 深さが 0 に戻る語(閉じの括弧)の手前まで — 開きの語の深さは 1。
    return [
        token for token, _ in takewhile(lambda pair: pair[1] > 0, zip(tokens, depths, strict=True))
    ]


def _hy_import_names(tokens: list[str]) -> Iterator[str]:
    """`(import a.b [c d :as e] f :as g)` の語の列 → a.b・a.b.c・a.b.d・f(:as の後の別名と keyword は名でない)。"""
    words = [
        token
        for index, token in enumerate(tokens)
        if not token.startswith(":") and (index == 0 or tokens[index - 1] != ":as")
    ]
    module: str | None = None
    depth = 0
    for token in words[2:]:
        if token in _OPEN:
            depth += 1
        elif token in _CLOSE:
            depth -= 1
        elif depth == 0:
            module = token.replace("-", "_")
            yield module
        elif depth == 1 and module is not None:
            yield f"{module}.{token.replace('-', '_')}"


def _hy_imports(text: str, top_level_only: bool) -> Iterator[str]:
    """Hy の source の `(import …)`・`(require …)` が読む module と、`[…]` で取り出す名の module.名。"""
    code = _hy_code_only(text)
    head = r"(?m)^\((?:import|require)\s" if top_level_only else r"\((?:import|require)\s"
    for found in re.finditer(head, code):
        yield from _hy_import_names(_hy_form(code, found.start()))


def _is_type_checking(node: ast.AST) -> bool:
    match node:
        case ast.If(test=ast.Name(id="TYPE_CHECKING") | ast.Attribute(attr="TYPE_CHECKING")):
            return True
        case _:
            return False


def _inner_statements(node: ast.AST) -> list[ast.stmt]:
    """節の直の下の文の列(関数・class・if・for・while・with・try・match の中)。"""
    return [
        statement
        for _, value in ast.iter_fields(node)
        if isinstance(value, list)
        for item in value
        for statement in (
            [item]
            if isinstance(item, ast.stmt)
            else item.body
            if isinstance(item, ast.ExceptHandler | ast.match_case)
            else []
        )
    ]


def _statements(body: list[ast.stmt], nested: bool) -> Iterator[ast.stmt]:
    """文の列とその中の文 — import は文にだけ現れるので、式は辿らない。`if TYPE_CHECKING:` の中は除く。

    nested でなければ import の時に走る文だけを辿る — 上の段の with・try・if・class の中は辿り、関数の中は辿らない(例 http_handlers.py
    は Hy と httpx の import を `with extra_needed(...)` の中に置く)。
    """
    for statement in body:
        if not _is_type_checking(statement):
            yield statement
            if nested or not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                yield from _statements(_inner_statements(statement), nested)


def _python_imports(text: str, package: str, top_level_only: bool) -> Iterator[str]:
    """Python の source の import が読む module と、`from m import n` の m.n(n が submodule か lazy な名の時のため)。

    `if TYPE_CHECKING:` の中は実行の時に読まないので除く。top_level_only なら import の時に走る文(上の段と、その with・try・if の
    中)だけを読む。
    """
    parts = package.split(".") if package else []
    body = ast.parse(text).body
    for node in _statements(body, nested=not top_level_only):
        match node:
            case ast.Import(names=names):
                yield from (alias.name for alias in names)
            case ast.ImportFrom(module=module, names=names, level=level):
                base = parts[: len(parts) - level + 1] if level else []
                where = ".".join([*base, *([module] if module else [])])
                yield where
                yield from (f"{where}.{alias.name}" for alias in names)


def _imports(path: Path, package: str, top_level_only: bool) -> Iterator[str]:
    text = path.read_text(encoding="utf-8")
    if not top_level_only and not _MENTIONS.search(text):
        # 部品の source の多くは Hy にも HTTP にも触れない — 解かずに飛ばす(相対の import は自分の部品の中なので拾わなくてよい)。
        return iter(())
    if path.suffix == ".hy":
        return _hy_imports(text, top_level_only)
    return _python_imports(text, package, top_level_only)


def _external(imported: str) -> frozenset[NormalizedName]:
    top = imported.split(".")[0]
    return frozenset({EXTERNAL[top]}) if top in EXTERNAL else NOTHING


@cache
def _core_effects_needs() -> dict[str, frozenset[NormalizedName]]:
    """doeff_core_effects の module(と lazy な名)→ import すると要る配布物(doeff-hy・hy・httpx の部分集合)。

    .hy の module は doeff-hy(Hy の import の hook を置く — _hy_submodules.py)を要る。.py の module は、上の段の import が読む
    物を要る(関数の中の遅い import は、`import doeff` を Hy 無しで通すための物なので数えない)。
    """
    files = [
        (path, path.relative_to(CORE_EFFECTS_DIR).with_suffix("").parts)
        for path in sorted(CORE_EFFECTS_DIR.glob("doeff_core_effects/**/*"))
        if path.suffix in {".py", ".hy"}
    ]
    modules = {
        ".".join(parts[:-1] if parts[-1] == "__init__" else parts): path for path, parts in files
    }
    imported = {
        module: frozenset(
            _imports(
                path,
                module if path.stem == "__init__" else module.rpartition(".")[0],
                top_level_only=True,
            )
        )
        for module, path in modules.items()
    }
    needs = {
        module: frozenset({DOEFF_HY} if path.suffix == ".hy" else ()).union(
            *map(_external, imported[module])
        )
        for module, path in modules.items()
    }
    while (
        grown := {
            module: needs[module].union(
                *(needs[other] for other in imported[module] & modules.keys())
            )
            for module in needs
        }
    ) != needs:
        needs = grown
    # `from doeff_core_effects import HttpRequest` などの遅い名(__init__.py の _LAZY_EXPORTS)は、Hy で書いた HTTP の
    # effect と handler を初めて使う時に読む。
    lazy = next(
        ast.literal_eval(node.value)
        for node in ast.parse(modules["doeff_core_effects"].read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)]
        == ["_LAZY_EXPORTS"]
    )
    return needs | {
        f"{where}.{name}": frozenset({DOEFF_HY}) | needs[module]
        for name, module in lazy.items()
        for where in ("doeff_core_effects", module)
    }


def _shipped_sources(directory: Path) -> Iterator[tuple[Path, str]]:
    """部品の配る source の file と、その file の在る package の名(src の下か、部品の dir の直下の package)。"""
    src = directory / "src"
    root = src if src.is_dir() else directory
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        top = root / relative.parts[0]
        if (
            path.suffix in {".py", ".hy"}
            and not NOT_SHIPPED & set(relative.parts)
            and (root == src or (top / "__init__.py").exists() or (top / "__init__.hy").exists())
        ):
            yield path, ".".join(relative.parts[:-1])


def _package_needs(directory: Path) -> frozenset[NormalizedName]:
    """部品の配る source が import の時に要る配布物(doeff-hy・hy・httpx の部分集合)。"""
    needs = _core_effects_needs()
    return NOTHING.union(
        *(
            frozenset({HY} if path.suffix == ".hy" else ()).union(
                *(
                    needs.get(imported, NOTHING) | _external(imported)
                    for imported in _imports(path, package, top_level_only=False)
                )
            )
            for path, package in _shipped_sources(directory)
        )
    )


def _as_used(name: NormalizedName) -> Requirement:
    """部品を使う時の入れ方(USED_WITH_EXTRA に在れば、その extra を足す)。"""
    return Requirement(f"{name}[{USED_WITH_EXTRA[name]}]" if name in USED_WITH_EXTRA else name)


def test_pip_install_doeff_brings_only_the_core_four() -> None:
    assert _installs([Requirement("doeff")]) == CORE_FOUR, (
        "pip install doeff で入る物が核の 4 個でない — Hy と HTTP の物は extra(hy・http・all)に置く"
    )


def test_the_extras_bring_hy_and_httpx_for_the_handlers_behind_them() -> None:
    # 値 = (doeff-hy が入るか, httpx が入るか)。http-server(aiohttp)と postgres(psycopg)の答え手も Hy で書いてある。
    expected = {
        "doeff[hy]": (True, False),
        "doeff[http]": (True, True),
        "doeff[all]": (True, True),
        "doeff-core-effects[hy]": (True, False),
        "doeff-core-effects[http]": (True, True),
        "doeff-core-effects[all]": (True, True),
        "doeff-core-effects[http-server]": (True, False),
        "doeff-core-effects[postgres]": (True, False),
    }
    installs = {line: _installs([Requirement(line)]) for line in expected}
    assert {line: (DOEFF_HY in got, HTTPX in got) for line, got in installs.items()} == expected


def test_each_workspace_package_brings_the_hy_and_httpx_it_imports() -> None:
    lacking = {
        name: missing
        for name, (directory, _) in sorted(_projects().items())
        if name not in NOT_SCANNED
        and (missing := sorted(_package_needs(directory) - _installs([_as_used(name)])))
    }
    assert not lacking, (
        "既定の依存が、import する物を入れない部品 → 足りない配布物(Hy の module は doeff[hy] か doeff-hy・"
        f"HTTP の client の handler は doeff-core-effects[http] を宣言する): {lacking}"
    )


def test_the_import_reader_finds_the_hy_and_http_modules_of_core_effects() -> None:
    # 読みが Hy の module を 1 つも拾わないと、上の検は黙って緑になる(例 .hy の import の形を読み違えた時)。
    needs = _core_effects_needs()
    expected = {
        "doeff_core_effects": NOTHING,
        "doeff_core_effects._effects": NOTHING,
        "doeff_core_effects.os_process": frozenset({DOEFF_HY}),
        "doeff_core_effects.cache": frozenset({DOEFF_HY}),
        "doeff_core_effects.http_handlers": frozenset({DOEFF_HY, HTTPX}),
        "doeff_core_effects.HttpRequest": frozenset({DOEFF_HY}),
        "doeff_core_effects.http_production_handler": frozenset({DOEFF_HY, HTTPX}),
    }
    assert {module: needs[module] for module in expected} == expected
