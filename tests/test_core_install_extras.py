"""Failure cases for `pip install doeff` installing only the 4 core components, with Hy and HTTP added by extras (milestone 8 of the doeff-publish initiative).

The 4 core components = doeff, doeff-vm, doeff-core-effects, frozendict. `import doeff` and the README Python examples work with these 4 alone
(option D marked with a star in the dependency report 2026-10-16-doeff-core-dependency-tree.html). The Hy language (doeff-hy, hy, pydantic ...) and httpx are added by extras:

- hy   = doeff-hy (needed by effects and handlers written in Hy, the cache, and the CLI's doeff run)
- http = doeff-hy and httpx (the HTTP client handler is written in Hy and sends with httpx)
- all  = hy and http (the same set as the default up to 0.5)

doeff's extras pull the extras of the same name in doeff-core-effects. The other extras of doeff-core-effects (http-server, postgres) also pull doeff-hy, because their responders
are written in Hy.

If a workspace component imports a doeff_core_effects module written in Hy (or a module that loads it at import time), or doeff_hy, hy, or httpx,
the component's default dependencies must install it — otherwise it fails on a user's machine with `No module named 'doeff_hy'` after installing from PyPI
(local development does not fail because the dev group installs both).

Dependencies are read from the declarations in pyproject.toml (verification with a real install is the CI test of card 3 of milestone 8).
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
# Leading import name -> the distribution that installs it.
EXTERNAL = {"doeff_hy": DOEFF_HY, "hy": HY, "httpx": HTTPX}
# Dirs of a component that are not distributed source (tests, examples, benchmarks).
NOT_SHIPPED = frozenset({"tests", "test", "examples", "benchmarks"})
# Components this test does not scan, with reasons.
NOT_SCANNED = {
    # The owner of the Hy modules. What its own modules need is read by _core_effects_needs below.
    canonicalize_name("doeff-core-effects"): "owner of the Hy modules",
    # The CLI's doeff run works without Hy and loads Hy by a lazy import only for --hy and .hy programs. If Hy is absent, it fails with a message naming
    # the extra to add (tests/cli/test_cli_without_hy.py, doeff_core_effects/_extra_hints.py).
    canonicalize_name("doeff"): "only the CLI's --hy and .hy programs load Hy",
    # A pytest plugin installed only as a dependency of doeff-hy (doeff-hy pulls doeff-hy-pytest — pulling the other way would form a cycle).
    canonicalize_name("doeff-hy-pytest"): "plugin pulled by doeff-hy",
}
# Component -> extras that satisfy its import reads (read on top of the default dependencies).
USED_WITH_EXTRA = {
    # Only the dogfood declaration module reads doeff-core-effects, and only under the dogfood extra (ADR-DOE-DOMAIN-001 D1).
    canonicalize_name("doeff-domain"): "dogfood",
}
_OPEN = frozenset("([{")
_CLOSE = frozenset(")]}")
# Strings and comments in Hy source (the one that starts first from the left wins — do not confuse a ; inside a string with a " inside a comment).
_HY_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|;[^\n]*')
_HY_TOKEN = re.compile(r"[\[\](){}]|[^\s\[\](){}]+")
# A marker for whether a component's source touches what the checks below read (files that do not are not parsed).
_MENTIONS = re.compile(r"doeff[_-]core[_-]effects|doeff[_-]hy|httpx|\bhy\b")


@cache
def _projects() -> dict[NormalizedName, tuple[Path, dict[str, object]]]:
    """Names of workspace components (including the root doeff) -> (component dir, the project table of pyproject.toml)."""
    tables = (
        (pyproject.parent, tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"])
        for pyproject in [REPO / "pyproject.toml", *REPO.glob("packages/*/pyproject.toml")]
    )
    return {canonicalize_name(project["name"]): (where, project) for where, project in tables}


def _lines(project: dict[str, object], extra: str | None) -> tuple[str, ...]:
    """PEP 508 strings of a component's default dependencies (extra is None) or of one extra's dependencies."""
    match extra, project:
        case None, {"dependencies": list() as lines}:
            return tuple(map(str, lines))
        case None, _:
            return ()
        case str(), {"optional-dependencies": dict() as extras} if extra in extras:
            return tuple(map(str, extras[extra]))
        case _:
            raise AssertionError(f"{project['name']} has no extra {extra!r} (pip silently skips it)")


def _keys(requirements: Iterable[Requirement]) -> frozenset[tuple[NormalizedName, str | None]]:
    """Dependency string -> a pair (distribution name, the extra to follow or the default None)."""
    return frozenset(
        (canonicalize_name(requirement.name), extra)
        for requirement in requirements
        for extra in (None, *requirement.extras)
    )


def _installs(requirements: Iterable[Requirement]) -> frozenset[NormalizedName]:
    """Names of what gets installed when requirements are installed — workspace components follow their default dependencies and the requested extras; external ones are not followed."""
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
    """Replace strings and comments in Hy source with spaces (keeping lines) — so the parentheses and import words inside are not picked up."""
    return _HY_STRING_OR_COMMENT.sub(lambda found: re.sub(r"[^\n]", " ", found.group()), text)


def _hy_form(code: str, start: int) -> list[str]:
    """The token sequence of one parenthesized form opened at start (including the opening and closing parentheses)."""
    # Read tokens only up to the close of the form (do not slice to the end of the file — re-reading the file for each import form is slow).
    tokens, counted = tee(found.group() for found in _HY_TOKEN.finditer(code, start))
    depths = accumulate(1 if token in _OPEN else -1 if token in _CLOSE else 0 for token in counted)
    # Up to just before the token where the depth returns to 0 (the closing parenthesis) — the depth at the opening token is 1.
    return [
        token for token, _ in takewhile(lambda pair: pair[1] > 0, zip(tokens, depths, strict=True))
    ]


def _hy_import_names(tokens: list[str]) -> Iterator[str]:
    """Token sequence of `(import a.b [c d :as e] f :as g)` -> a.b, a.b.c, a.b.d, f (aliases after :as and keywords are not names)."""
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
    """Modules read by `(import …)` and `(require …)` in Hy source, and module.name for names taken out with `[…]`."""
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
    """The sequence of statements directly under a clause (inside function, class, if, for, while, with, try, match)."""
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
    """The sequence of statements and the statements inside them — imports appear only in statements, so expressions are not traversed. The inside of `if TYPE_CHECKING:` is excluded.

    Unless nested, only statements that run at import time are traversed — the insides of top-level with, try, if, and class are traversed, but not function bodies (e.g. http_handlers.py
    puts its Hy and httpx imports inside `with extra_needed(...)`).
    """
    for statement in body:
        if not _is_type_checking(statement):
            yield statement
            if nested or not isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                yield from _statements(_inner_statements(statement), nested)


def _python_imports(text: str, package: str, top_level_only: bool) -> Iterator[str]:
    """Modules read by imports in Python source, and m.n of `from m import n` (for when n is a submodule or a lazy name).

    The inside of `if TYPE_CHECKING:` is not read at runtime and is excluded. With top_level_only, only statements that run at import time (the top level and the insides of its with, try, and if)
    are read.
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
        # Most component sources touch neither Hy nor HTTP — skip them without parsing (relative imports are inside the component itself, so they need not be picked up).
        return iter(())
    if path.suffix == ".hy":
        return _hy_imports(text, top_level_only)
    return _python_imports(text, package, top_level_only)


def _external(imported: str) -> frozenset[NormalizedName]:
    top = imported.split(".")[0]
    return frozenset({EXTERNAL[top]}) if top in EXTERNAL else NOTHING


@cache
def _core_effects_needs() -> dict[str, frozenset[NormalizedName]]:
    """doeff_core_effects modules (and lazy names) -> the distributions needed when imported (a subset of doeff-hy, hy, httpx).

    .hy modules need doeff-hy (which installs the Hy import hook — _hy_submodules.py). A .py module needs what its top-level imports read
    (lazy imports inside functions exist so that `import doeff` passes without Hy, so they are not counted).
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
    # Lazy names such as `from doeff_core_effects import ReadText` (_LAZY_EXPORTS in __init__.py) load the module that provides the name on first
    # use. A name that the module does not bind at the top level but provides lazily through its own __getattr__ (HttpRequest in _effects — an HTTP
    # effect written in Hy) also needs Hy. Names of .py modules bound at the top level (memo and cache effects and handlers) need only what the module needs.
    lazy = next(
        ast.literal_eval(node.value)
        for node in ast.parse(modules["doeff_core_effects"].read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)]
        == ["_LAZY_EXPORTS"]
    )
    return needs | {
        f"{where}.{name}": needs[module]
        | (NOTHING if name in _bound_names(modules[module]) else frozenset({DOEFF_HY}))
        for name, module in lazy.items()
        for where in ("doeff_core_effects", module)
    }


def _bound_names(path: Path) -> frozenset[str]:
    """Names bound at the top level of a .py module (def, class, assignment, import). Names provided lazily by the module's __getattr__ are not included.

    Empty for a .hy module (a .hy module needs Hy by itself — held by needs above).
    """
    if path.suffix != ".py":
        return frozenset()
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        match node:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
                names.add(name)
            case ast.Assign(targets=targets):
                names.update(target.id for target in targets if isinstance(target, ast.Name))
            case ast.AnnAssign(target=ast.Name(id=name)):
                names.add(name)
            case ast.Import(names=aliases) | ast.ImportFrom(names=aliases):
                names.update((alias.asname or alias.name).split(".")[0] for alias in aliases)
            case _:
                pass
    return frozenset(names)


def _shipped_sources(directory: Path) -> Iterator[tuple[Path, str]]:
    """Source files distributed by a component, and the name of the package containing each file (under src, or a package directly under the component dir)."""
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
    """The distributions that a component's distributed source needs at import time (a subset of doeff-hy, hy, httpx)."""
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
    """How to install a component when using it (if it is in USED_WITH_EXTRA, add that extra)."""
    return Requirement(f"{name}[{USED_WITH_EXTRA[name]}]" if name in USED_WITH_EXTRA else name)


def test_pip_install_doeff_brings_only_the_core_four() -> None:
    assert _installs([Requirement("doeff")]) == CORE_FOUR, (
        "what pip install doeff installs is not the 4 core components — put Hy and HTTP items in extras (hy, http, all)"
    )


def test_the_extras_bring_hy_and_httpx_for_the_handlers_behind_them() -> None:
    # Value = (whether doeff-hy is installed, whether httpx is installed). The responders of http-server (aiohttp) and postgres (psycopg) are also written in Hy.
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
        "components whose default dependencies do not install what they import -> missing distributions (declare doeff[hy] or doeff-hy for Hy modules, "
        f"and doeff-core-effects[http] for the HTTP client handler): {lacking}"
    )


def test_the_import_reader_finds_the_hy_and_http_modules_of_core_effects() -> None:
    # If the reading picks up no Hy module at all, the test above would silently go green (e.g. when the form of .hy imports is misread).
    needs = _core_effects_needs()
    expected = {
        "doeff_core_effects": NOTHING,
        "doeff_core_effects._effects": NOTHING,
        "doeff_core_effects._os_process": frozenset({DOEFF_HY}),
        "doeff_core_effects._cache": frozenset({DOEFF_HY}),
        "doeff_core_effects._http_handlers": frozenset({DOEFF_HY, HTTPX}),
        "doeff_core_effects.HttpRequest": frozenset({DOEFF_HY}),
        "doeff_core_effects.http_production_handler": frozenset({DOEFF_HY, HTTPX}),
    }
    assert {module: needs[module] for module in expected} == expected
