"""doeff-hy-check — expand Hy source with the real macros, check types with pyright, and report red diagnostics at .hy lines.

    doeff-hy-check [PATH ...] [--root DIR] [--import-root DIR ...] [--pyright CMD] [--python EXE] [--json]
                   [--strict] [--baseline JSON | --write-baseline JSON] [--cache-dir DIR | --no-cache]
                   [--carry JSON [--carry-refresh-runs N]]

- PATH: the .hy file or dir to check (default = --root). A dir means every .hy under it.
- --root: the repo root (default = the current dir). It is the import root, and macro `require` is resolved from it too.
- --import-root: another import root under the root (e.g. `clients/hy`). The root's pyright config
  `extraPaths` are also read as import roots.
- --strict: set pyright's typeCheckingMode to strict.
- --write-baseline / --baseline: write the current reds as a baseline / stop only on reds absent from the baseline (doeff_hy/_static_baseline.py).
  Each --json diagnostic carries the field `known`, saying whether the red is in the baseline.
- --cache-dir / --no-cache: store the expansion of dependency .hy files, together with the record of the source and of the files the expansion passed through, and read it back (doeff_hy/_static_cache.py,
  default location = $XDG_CACHE_HOME/doeff-hy-check or ~/.cache/doeff-hy-check).
- --carry: the carry file (doeff_hy/_static_carry.py). A .hy whose inputs (its expansion, the declaration surface of the names it uses, where its imports resolve, the environment) are
  unchanged since the previous run is not given to pyright; the previous run's diagnostics for that file are carried over, and the file is replaced by this run's carry (no file = the first run, which checks everything).
  --carry-refresh-runs: even a file with unchanged inputs is re-checked once within this many runs (a review — default 12).
- Exit 1 if there is any new red (every red when there is no baseline), 0 if none, 2 if the tool could not run.

How it works:
0. Between expansion and pyright, hold one slot of the host check slot, shared with the other checks running on the same host (if it is full, wait; if the wait limit is exceeded, exit 2
   without running — doeff_hy/_check_slot.py. The slot is not taken on a host without the authoritative slot source).
1. Expand each .hy with Hy's compiler (doeff-hy macros run under the "expansion for type checking" switch in
   doeff_hy/_static_view.py). Imports in the source are not executed
   (only the macro `require`, as always with Hy's compile, imports the macro module).
2. Expand in the same way the other .hy files under the root that the checked file imports (to resolve types; no reds are reported for them). The places searched are the import roots and
   the parents from the importing file's dir up to the root (the same search as pyright — search_dirs).
3. In a temporary tree that mirrors the root's contents with symlinks, place the expanded Python as `<name>.py`,
   copy the root's pyright config, and run pyright. The consuming repo's files are not changed.
4. Map each pyright diagnostic from the expanded Python position back to the Hy expression position (macros also give
   synthesized expressions the position of the user expression that wraps them — doeff_hy/_positions.hy).

Settings: use the root's `pyrightconfig.json` (or `[tool.pyright]` in `pyproject.toml` if absent) as the base, and
raise only `reportDeprecated` to error (to catch a Program / effect placed in statement position with
the deprecated overload in doeff_hy/macros.pyi).

What is caught and what is not (the list measured on agora-controllers samples on 2026-09-23 is in
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
from doeff_hy_bytecode_guard._import_scope import SharedScope
from doeff_hy_bytecode_guard._loader_hooks import (
    gensym_renaming,
    macro_recording,
    record_is_current_here,
    value_reference,
)
from hy.compiler import hy_compile
from hy.errors import HyLanguageError
from hy.models import Object

from doeff_hy._check_slot import SlotExpired, slot_label, take_slot
from doeff_hy._env_places import (
    PYRIGHT_THREADS_ENV,
    conversation_setting,
    pyright_threads_setting,
)
from doeff_hy._static_baseline import (
    BaselineUnreadable,
    Split,
    baseline_json,
    errors_of,
    read_baseline,
    split,
)
from doeff_hy._static_cache import (
    BundleRow,
    CachedFinding,
    CachedProjection,
    CachedSpan,
    CacheMiss,
    FileStat,
    PackedSpans,
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
from doeff_hy._static_carry import (
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
from doeff_hy._static_view import (
    DEFEFFECT_OUTCOMES_MODULE,
    STATIC_HELPER_IMPORTS,
    collect_findings,
    static_view,
)
from doeff_hy._binding_forms import Finding, ModuleScope
from doeff_hy._expansion_names import PREFIX as _EXPANSION_PREFIX
from doeff_hy._expansion_names import is_alias_import

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._records import MacroRecord

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


#: Ranges of the expanded Python (0-based lines and columns, end exclusive) paired with the corresponding Hy positions (1-based). The same single type as the stored form —
#: so that when a stored expansion is read back into a Projection, hundreds of thousands of positions are not rebuilt.
Span = CachedSpan


@dataclass(frozen=True)
class Projection:
    source: Path
    module: str
    text: str
    spans: Sequence[Span]
    # Findings that macros emitted during expansion (val / var checks — ADR-DOE-HY-006), turned into diagnostics at Hy positions.
    findings: tuple[Diagnostic, ...] = ()
    # The record of a successful expansion (Hy version; the macro, helper and type-check post-processing files with their sha256 — used to validate the stored entry).
    used: "MacroRecord" = dataclasses.field(kw_only=True)


@dataclass(frozen=True)
class CompileFailure:
    source: Path
    diagnostic: Diagnostic


@dataclass(frozen=True)
class Closure:
    """The expansion of the checked files and of the .hy files under the root that they import."""

    projections: dict[Path, Projection]
    failures: list[CompileFailure]


@dataclass(frozen=True)
class HyPosition:
    line: int
    column: int


@dataclass(frozen=True)
class Checked:
    """Diagnostics, and notes that are not reds."""

    diagnostics: list[Diagnostic]
    notes: list[str]


def _absolute(path: Path) -> Path:
    """Normalize to an absolute path. Symlinks are not followed (so even a tree that mirrors the root with symlinks stays inside the root)."""
    return Path(os.path.normpath(path.absolute()))


def in_root(root: Path, path: Path) -> Path | None:
    """Map a named path under the root's spelling (_absolute — an absolute path without following symlinks). If the spelling is under the root, keep it as is; if following symlinks
    lands under the root, map it back to the root's spelling (Path.cwd(), which joins relative paths, returns the followed spelling); otherwise
    None (outside the root)."""
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
    """Make a module name from the path relative to the deepest import root."""
    base = max((r for r in roots if source.is_relative_to(r)), key=lambda r: len(r.parts))
    parts = list(source.relative_to(base).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _child_pairs(generated: ast.AST, original: ast.AST) -> Iterator[tuple[ast.AST, ast.AST]]:
    """Walk the expanded tree and the tree re-read from its string form in the same order. If the shapes diverge, do not descend there."""
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
    """Pair the range of each node in the re-read tree with the Hy position of the corresponding original node (the one Hy gave a position)."""
    g_line, g_col = _position(generated, "lineno"), _position(generated, "col_offset")
    g_end_line = _position(generated, "end_lineno")
    g_end_col = _position(generated, "end_col_offset")
    h_line, h_col = _position(original, "lineno"), _position(original, "col_offset")
    if g_line is None or g_col is None or g_end_line is None or g_end_col is None:
        return None
    if h_line is None or h_col is None:
        return None
    # Hy puts a 1-based column into col_offset (it mirrors the positions in hy.compiler).
    return Span((g_line - 1, g_col), (g_end_line - 1, g_end_col), h_line, max(h_col, 1))


#: Modules used only by definition bookkeeping (attributes for runtime introspection — they carry no type meaning). If no user remains after expansion,
#: the import is removed (under pyright strict, doeff_hy._quoted_forms had no stub and doeff_hy._declarations was a duplicate import —
#: 241 reds across 3 core files of the agora screen). doeff_hy._pytest_items is used only for
#: the pytest item records (added by deftest, defadr, defsemgrep and pytestmark — bookkeeping for collection) (a red for a missing stub — #2214).
BOOKKEEPING_MODULES: frozenset[str] = frozenset(
    {"doeff_hy._declarations", "doeff_hy._quoted_forms", "doeff_hy.record", "doeff_hy._pytest_items"}
)


def _bookkeeping_statement(statement: ast.stmt) -> bool:
    """Is this a module-level statement to hide from type checking: the definition bookkeeping `setattr(name, '__doeff_…__', …)`, the pytest item record
    `doeff_hy._pytest_items.record_at_import(globals(), …)` (added by `_record_form` in doeff_hy/_pytest_items.py — bookkeeping for
    collection), and what is left after Hy compiles `(require …)`, `hy.macros.require(…)`
    (macros were already imported at expansion time)."""
    match statement:
        case ast.Expr(
            value=ast.Call(func=ast.Name(id="setattr"), args=[_, ast.Constant(value=str(attribute)), *_])
        ):
            return attribute.startswith("__doeff_")
        case ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Attribute(value=ast.Name(id="doeff_hy"), attr="_pytest_items"),
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
    """Shrink the definition bookkeeping `setattr(name, '__doeff_effects__', doeff_hy._declarations.effect_types(place, tuple([A, B])))` into the statement
    `setattr(name, '__doeff_effects__', (A, B))`, which does not call doeff_hy._declarations (None for anything else).
    This keeps the names of effects that appear only in the declaration :effects (effects a handler clause emits through a helper function) among the names read in type checking —
    dropping the whole bookkeeping would make the import of such a name a strict reportUnusedImport red, which the author cannot fix (deleting the import would leave
    the :effects declaration unable to resolve the name at run time). An empty declaration `:effects []` (emits no effect) is also kept, as `()`:
    the tool static_stub makes the effect position of a Program in a .pyi answer Never (there is no name to be read)."""
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
    """Names read in a statement (for an attribute chain, the root name) — to decide which imports may be removed."""
    return frozenset(
        node.id
        for statement in statements
        for node in ast.walk(statement)
        if isinstance(node, ast.Name)
    )


def _bound_name(alias: ast.alias) -> str:
    """Names bound by an import (`import a.b` binds `a`; with `as`, that name)."""
    return alias.asname or alias.name.split(".")[0]


def _dotted(node: ast.expr) -> str | None:
    """The spelling of a name or an attribute chain starting from a name (`a.b.c`) — None for any other expression."""
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(value=value, attr=attr):
            base = _dotted(value)
            return None if base is None else f"{base}.{attr}"
        case _:
            return None


def _referenced_paths(statements: list[ast.stmt]) -> frozenset[str]:
    """Names and attribute chains read in a statement (reading `a.b.c` gives all of `a`, `a.b`, `a.b.c`) — to decide by
    module spelling whether `import a.b` is read. `import doeff_hy._declarations` and `import doeff_hy.record` bind the same root name `doeff_hy`,
    so with the root name alone, when only one of them is read, both would be kept (the expansion of defrecord :check)."""
    return frozenset(
        path
        for statement in statements
        for node in ast.walk(statement)
        if isinstance(node, (ast.Name, ast.Attribute))
        if (path := _dotted(node)) is not None
    )


def _alias_read(alias: ast.alias, used: frozenset[str]) -> bool:
    """Is one name of an import read (with `as`, that name; otherwise the spelling as written — `import a.b` is the chain `a.b`,
    `from m import x` is `x`)."""
    return (alias.asname or alias.name) in used


def _without_unused_bookkeeping(statement: ast.stmt, used: frozenset[str]) -> ast.stmt | None:
    """From the imports of bookkeeping-only modules (BOOKKEEPING_MODULES), remove the names no longer read, one at a time. If no name is left, remove
    the whole statement (None). This was for statements that list names that are read and names that are not (`import doeff_hy._declarations, doeff_hy.record` — the
    expansion of defrecord), where only the unread side became a strict reportUnusedImport."""
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
    """Is this a doeff-hy module itself (the target of the imports that macros synthesize for each definition)."""
    return module.split(".")[0] == "doeff_hy"


def _synthesized_import(statement: ast.stmt) -> bool:
    """Is this an import statement that a macro synthesizes for each definition (a user's duplicate import must stay red, so only these are collapsed to one):
    imports of doeff-hy's own modules (`from doeff_hy.<m> import …` only when every bound name is hidden — macros bind the names they pull from doeff-hy
    under a `_` name, such as handle's `_doeff_…` clause endings and the `doeff_hy.macros` helpers; an author's `(import doeff_hy._declarations [declared-value])`
    in a defmacro or defn body binds a public name and is a local of that function, so moving it to module level made the generated .pyi
    re-export names the runtime module does not have), and
    `import <module> as _doeff_<name>`, which binds a module under an alias with the expansion-name prefix `_doeff_` (expansion_names.PREFIX) (the form emitted by definition macros of the owning package — the imports of the system type and
    constructor modules that doeff-cluster's defsystem emits per system. doeff-hy does not hold the owning module's spelling), and the `import doeff_core_effects._outcomes` (static_view.DEFEFFECT_OUTCOMES_MODULE) that defeffect
    emits when it places the way answers are divided, and `from doeff import Pass as _doeff_Pass`, where handle and defhandler bind a
    node under an alias `_doeff_<name>`, and `from typing import ClassVar as _doeff_ClassVar`, which a type-building definition reads in the type body, and the like (expansion_names.is_alias_import — only spellings in a closed table. If left
    inside a function or class body, then in a class body the clause functions and the bodies of nested types looked names up from the module level, and pyright read them as undefined).
    The only `_doeff_` alias an author writes is the for/do `(import doeff [do :as _doeff-do])` (the `from … import` form), so
    the form `import <module> as _doeff_<name>` is never confused with an author's import."""
    match statement:
        case ast.Import(names=names):
            return all(
                _doeff_hy_module(alias.name)
                or (alias.name == DEFEFFECT_OUTCOMES_MODULE and alias.asname is None)
                or (alias.asname is not None and alias.asname.startswith(_EXPANSION_PREFIX))
                for alias in names
            )
        case ast.ImportFrom(module=str(module), level=0, names=names):
            return all(
                (_doeff_hy_module(module) and (alias.asname or alias.name).startswith("_"))
                or is_alias_import(module, alias.name, alias.asname)
                for alias in names
            )
        case _:
            return False


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _nested_imports(node: ast.AST, inside: bool) -> Iterator[ast.stmt]:
    """Return, in source order, the macro-synthesized import statements (`_synthesized_import`) that sit inside function or class bodies within node (including nested statements).
    inside = whether node is already inside a function or class body."""
    here = inside or isinstance(node, _SCOPES)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt) and here and _synthesized_import(child):
            yield child
        else:
            yield from _nested_imports(child, here)


#: Nodes that have a statement body (`body`) — so that a body emptied by removing imports gets a `pass` (an empty body cannot be written as a Python statement).
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
    """To remove the macro-synthesized import statements inside function and class bodies (what `_nested_imports` returns). A body that
    held only that one statement becomes `pass`."""

    def __init__(self) -> None:
        self._mut_depth = 0

    def _scope(self, node: ast.AST) -> ast.AST:
        """Raise the depth by one only while reading inside a function or class body (imports inside are the removal targets — module-level ones are not removed)."""
        self._mut_depth += 1
        try:
            return self.generic_visit(node)
        finally:
            self._mut_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        """Read inside a function body (`_scope`)."""
        return self._scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        """Read inside an async function body (`_scope`)."""
        return self._scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        """Read inside a class body (`_scope`)."""
        return self._scope(node)

    def visit_Import(self, node: ast.Import) -> ast.Import | None:
        """Remove macro-synthesized `import` inside function and class bodies (what `_nested_imports` gathered to the module level)."""
        return None if self._mut_depth and _synthesized_import(node) else node

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.ImportFrom | None:
        """Remove macro-synthesized `from … import` inside function and class bodies (the same as `visit_Import`)."""
        return None if self._mut_depth and _synthesized_import(node) else node

    def generic_visit(self, node: ast.AST) -> ast.AST:
        """Read the children, and put `pass` in a node whose body became empty after imports were removed."""
        visited = super().generic_visit(node)
        if isinstance(node, _BODIED) and not node.body:
            node.body = [ast.Pass()]
        return visited


def with_module_level_imports(tree: ast.Module) -> ast.Module:
    """Move the imports that macros placed inside function and class bodies (`_synthesized_import` — doeff-hy modules, modules bound under a `_doeff_` alias, and the module for defeffect's
    way of dividing answers) to before the statement directly under the enclosing module (the type-checking-expansion side of `_scoped-definition` in macros.hy).

    Definition macros put the expansion's imports where they wrote them. An `import doeff_hy.record` inside a function makes `doeff_hy` a local name of the whole outer function,
    and if the outer function first reads the module-level `doeff_hy`, pyright reads it as unbound. An import in a class body is a class attribute, and the bodies of nested types
    and methods look names up from the module level (as in Python), so the `doeff_hy.record` that the definition reads cannot be resolved. The runtime expansion
    wraps the inside of the function to confine local names (`_scoped-definition`), but the type-checking expansion leaves the type declarations (class, def) in the outer function, so it cannot wrap —
    gather the imports at module level and leave the rest to the later cleanup (folding duplicates in `without_bookkeeping`, and removing imports of unread bookkeeping).
    Statements at module level and imports inside a module-level `if` are not moved (they are already module-level names). The tree belongs to this expansion alone, so it is replaced in place."""
    moved = [tuple(_nested_imports(statement, inside=False)) for statement in tree.body]
    _WithoutNestedImports().visit(tree)
    tree.body = [
        placed
        for imports, statement in zip(moved, tree.body, strict=True)
        for placed in (*imports, statement)
    ]
    return tree


def with_canonical_gensyms(tree: ast.Module) -> ast.Module:
    """Renumber the Hy gensym names in the expansion tree (identifiers and string constants) from 1, in order of count within the file.

    The gensym counter is one per process, so even for the same source the names changed depending on what was expanded first in the same process, or on which process
    expanded it when run in parallel, and the stored content depended on how the run was done. The renumbering rule is the same single one as the canonicalization of code on the test-run side
    (doeff_hy_bytecode_guard.gensym_renaming). The replacement is one to one, so name uniqueness is preserved. The tree belongs to this expansion alone,
    so it is replaced in place."""
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
    """One string in an AST field (an identifier, a string constant, or the list of names of global / nonlocal)."""
    match value:
        case str():
            return (value,)
        case list():
            return tuple(item for item in value if isinstance(item, str))
        case _:
            return ()


def without_bookkeeping(tree: ast.Module) -> ast.Module:
    """From the type-checking expansion, remove the bookkeeping that carries no type meaning (cutting off at the expansion side the source of pyright strict's false positives).

    - Remove the definition bookkeeping and what is left of `hy.macros.require` (_bookkeeping_statement). However, the :effects declaration bookkeeping is shrunk into a form that reads the names
      and kept (_effects_reads — so that an import of an effect name appearing only in the declaration does not become an unread import).
    - If the import statements that macros emit per definition directly under a module (doeff-hy modules, and modules bound under a `_doeff_` alias —
      _synthesized_import) repeat, keep only the first.
    - Remove names no longer read from imports of bookkeeping-only modules (BOOKKEEPING_MODULES) (names listed in one statement are handled one by one) and from Hy's `import hy`.
    Expressions the consuming repo's user wrote are not removed (only the statement shapes a macro synthesized are)."""
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
    """Is this the `import hy` that Hy's compiler places in each module, which is no longer read after the bookkeeping is removed
    (only what was left of `hy.macros.require` read it — the source of a strict reportUnusedImport)."""
    match statement:
        case ast.Import(names=[ast.alias(name="hy", asname=None)]):
            return "hy" not in used
        case _:
            return False


def with_static_helpers(tree: ast.Module) -> ast.Module:
    """Place the imports of the helper names that statically-expanded macros refer to once at the head of the module (after `from __future__`).

    In static expansion, macros do not emit an import per defk / defhandler / `<-` (see the note on static_view.STATIC_HELPER_IMPORTS —
    when the declarations of one name exceed 64, pyright stops type inference). The statements placed are not in the Hy source, so they get no position
    (_span does not pair them — no red appears on a helper import). Only the helper names the module reads are placed (so as not to produce strict
    reportUnusedImport). For a module that reads `hy.models` (where Hy keyword literals are compiled to), add
    `import hy.models` (with `import hy` alone, pyright does not know the attribute models)."""
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
    """From the helper import statements, keep only the names the module reads (if none is read, the whole statement is unneeded = None).
    `import pytest as _doeff_pytest` (which the deftest decorator draws on) is also placed only when read. `import hy.models` binds `hy`, and
    since the caller adds it only when read, it is kept by this test as well."""
    match statement:
        case ast.ImportFrom(module=module, names=names, level=level):
            kept = [alias for alias in names if _bound_name(alias) in used]
            return ast.ImportFrom(module=module, names=kept, level=level) if kept else None
        case ast.Import(names=names):
            kept = [alias for alias in names if _bound_name(alias) in used]
            return ast.Import(names=kept) if kept else None
        case _:
            return statement


def _parses(statement: ast.stmt) -> bool:
    """Turn one statement back into text: can it be re-read with Python's grammar?"""
    try:
        ast.parse(ast.unparse(statement))
    except SyntaxError:
        return False
    return True


def _nested_statements(node: ast.AST) -> list[ast.stmt]:
    """The statements directly under node (body, else, finally, except and case clause bodies — nested statements are not entered)."""
    def own(child: ast.AST) -> list[ast.stmt]:
        """The statements that one child brings directly under node (a clause descends into its body; an expression holds no statement)."""
        match child:
            case ast.stmt():
                return [child]
            case ast.excepthandler() | ast.match_case():
                return _nested_statements(child)
            case _:
                return []

    return [statement for child in ast.iter_child_nodes(node) for statement in own(child)]


def _unparsable_statement(statements: list[ast.stmt]) -> ast.stmt | None:
    """The innermost statement that cannot be re-read when turned back into text (None if all of statements can be re-read)."""
    for statement in statements:
        if not _parses(statement):
            return _unparsable_statement(_nested_statements(statement)) or statement
    return None


def _unparsable_position(statements: list[ast.stmt]) -> HyPosition:
    """The Hy position a diagnostic points at when the expansion does not fit the grammar: the position of the innermost statement that cannot be re-read (the head of the file if none is found)."""
    culprit = _unparsable_statement(statements)
    if culprit is None:
        return HyPosition(1, 1)
    return HyPosition(_position(culprit, "lineno") or 1, max(_position(culprit, "col_offset") or 1, 1))


def project(root: Path, roots: list[Path], source: Path) -> Projection | CompileFailure:
    """Turn one .hy into Python by the type-checking expansion, and build the position correspondence table."""
    text = source.read_text(encoding="utf-8")
    name = module_name(roots, source)
    module = ModuleType(name)
    module.__file__ = str(source)
    relative = str(source.relative_to(root))
    # Read the source with the Hy reader exactly once. compile processes the previous form before reading the next (so a reader macro
    # defined in the file takes effect on later forms), so we do not read everything first; each time compile reads one form we show the same form to the
    # findings of the outermost setv (ModuleScope), so no form is held to the end. Previously the findings re-read with a fresh reader, paying
    # about 20% of the expansion, and failed without knowing the reader macros in the file.
    forms = hy.read_many(text, filename=str(source))
    scope = ModuleScope()
    lazy = hy.models.Lazy(_shown_to(scope, forms))
    lazy.source, lazy.filename, lazy.reader = forms.source, forms.filename, forms.reader
    try:
        with static_view(), collect_findings() as found, macro_recording(module) as recording:
            compiled = hy_compile(lazy, module, filename=str(source), source=text)
        # hy_compile returns a (Module, Expression) pair only when get_expr=True.
        if not isinstance(compiled, ast.Module):
            raise TypeError(f"hy_compile did not return a module: {type(compiled).__name__}")
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
    except Exception as error:  # a failure in post-processing outside macros also counts as the failure of one file — get out of the child process without taking down the whole check
        return CompileFailure(
            source,
            Diagnostic(relative, 1, 1, "error", "hy-expansion", f"expansion failed: {type(error).__name__}: {error}"),
        )
    rendered = ast.unparse(tree)
    try:
        reparsed = ast.parse(rendered)
    except SyntaxError as error:
        # Hy emitted a tree that Python's grammar does not allow (e.g. `case A(b-c=x)`, where Hy does not mangle the name of a match type pattern field). 
        # Previously the SyntaxError escaped from the child process and took down the check of the whole repo.
        at = _unparsable_position(tree.body)
        return CompileFailure(
            source,
            Diagnostic(
                relative, at.line, at.column, "error", "hy-expansion", f"the expanded Python is not valid syntax: {error.msg}"
            ),
        )
    # Position correspondences are held in packed arrays (the pickle sent from the expansion child to the parent, and what the parent holds during pyright, are each one array — PackedSpans).
    spans = PackedSpans.of(span for pair in _child_pairs(reparsed, tree) if (span := _span(*pair)) is not None)
    # A setv directly under a module is outside macros, so look at the outermost sequence of the source separately (ADR-DOE-HY-006) — findings seen alongside compile.
    top_level = scope.findings
    return Projection(
        source,
        name,
        rendered,
        spans,
        tuple(_finding_diagnostic(relative, f) for f in (*found, *top_level)),
        used=recording.record(
            text, also=(value_reference(_POST_PROCESSING_MODULE, "POST_PROCESSING"),)
        ),
    )


#: The entry point of the processing outside macros that changes the expansion result (moving imports in function and class bodies to module level, removing bookkeeping, adding helper imports, renumbering gensym names, position correspondence, findings) —
#: the record in the type-check expansion store (static_cache) sees changes in post-processing through the digest of this value's closure (following the functions that ``project`` calls, by name).
#: It holds the function as defined, not the module attribute ``project`` (so even if a check swaps ``project``, the record does not change).
POST_PROCESSING = (project,)

#: The module name by which the record names POST_PROCESSING — a name that can be resolved by import (``__spec__.name``). When launched with ``python -m doeff_hy._static_check``,
#: this file is read as ``__main__`` in the parent and as ``__mp_main__`` in the expansion child; naming it by ``__name__`` made the child's record (the digest following
#: ``project`` in ``__mp_main__``) and the parent's check in the next run (the digest following ``project`` in ``__main__``) follow functions under different owner names and
#: not match, so not one stored expansion could be read back. With a name that can be resolved by import, parent and child follow
#: the same file under that name.
_POST_PROCESSING_MODULE = __spec__.name if __spec__ is not None else __name__


def _shown_to(scope: ModuleScope, forms: Iterable[Object]) -> Iterator[Object]:
    """Hand over forms one at a time, showing each form to the scope before handing it over (so compile and the findings share a single read)."""
    for form in forms:
        scope.visit(form)
        yield form


def _finding_diagnostic(relative: str, finding: Finding) -> Diagnostic:
    """Turn a macro finding (binding_forms.Finding) into this tool's diagnostic (red or warning, rule name, wording)."""
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
    """The names of the files directly under dir and the names of the dirs (both empty if the dir is missing or unreadable — as with Path.is_file, what is missing is not a file)."""

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
    """The absolute path of the candidate files that actually exist among the candidates for import name `name` (<name>.hy and <name>/__init__.hy). Descend one dir at a time, and do not
    look past a dir that does not exist (no stat per candidate; the listing of the same dir is fetched once by the caller's memo)."""
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
    """The dirs in which to look for the .hy of an absolute import that the source writes, in search order (the one place for the search path of dependency expansion).

    After the import roots (import_roots — the repo root and extraPaths), place the dirs reached by walking up from the source's dir to the root. When pyright cannot resolve an
    absolute import via the root, extraPaths, the standard library, or the environment, it searches the parent dirs in order from the importing file's dir toward the root
    (`(import e2e_wire …)` of a module next to a script run as `hy scripts/x.hy` is resolved this way). Previously only the import roots were searched, so
    when checking a single file the neighboring .hy was not expanded, and pyright could not resolve the import (reportMissingImports, and the
    Unknown reds that follow). When a whole dir was checked, the neighboring .hy was expanded as a file to be checked, so it resolved — the answer for the same file
    differed depending on how it was checked.

    Expansion collects every candidate that exists (it keeps searching later dirs even after a hit in an earlier one). An expansion pyright does not read (a neighboring .hy with the same name as a standard
    library module, for example — pyright resolves the standard library first) is merely an extra expansion and does not change the reds of the checked file
    (when a whole dir is checked, that .hy is expanded and in the tree as well)."""
    local = [d for d in (source.parent, *source.parent.parents) if d.is_relative_to(root)]
    return list(dict.fromkeys([*roots, *local]))


def import_roots(root: Path, settings: dict[str, object]) -> list[Path]:
    """Import roots = the repo root + those of pyright's extraPaths that are under the root."""
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
    """Turn a stored expansion back into this run's Projection (to avoid expanding again)."""
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
    """Turn the expansion result into the stored form (so the next run does not expand the dependency again)."""
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


#: The number of child processes that run the expansion of dependencies not found in the store — held by the declaration of the host check slot (the authoritative public entry process_width answers it,
#: and it arrives through SlotHeld.width). This constant is the default for hosts where the declaration does not arrive (a host with no authoritative source, an old source, a declaration that cannot be read), not the owner of the value.
#: The actual number is min(this number, the number of CPUs this process can use, the number of unfound files in that stage).
#: 4 (2026-10-11): on a 36-core host, 2 checks on the same working tree and 4 other checks overlapped and the load reached 163 (over 30 expansion children and forkservers).
#: There is no environment variable to change it — with one, only the check of a conversation that changed the value would count with a different number, and the way of counting slots would split (see the note at the head of the authoritative source).
EXPANDER_LIMIT = 4


def expander_limit(declared: int | None) -> int:
    """Decide the upper bound on the number of expansion children (declared = the number of parallel runs within one slot that the slot's authoritative source answered; if it did not arrive, None gives EXPANDER_LIMIT)."""
    return EXPANDER_LIMIT if declared is None else declared


class ExpansionWorkerLostError(RuntimeError):
    """The child process in charge of dependency expansion crashed without returning an answer (names the files with no answer — main turns this into a tool failure, exit 2)."""


@dataclass(frozen=True)
class _Hit:
    """An expansion found in the store, the names of the modules it imports, and the line to write into the bundle."""

    projection: Projection
    imports: frozenset[str]
    relative: str
    row: BundleRow


@dataclass(frozen=True)
class _Miss:
    """A source not found in the store, the key under which to store it after expansion (None in a run that does not store), and what the bundle line needs."""

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
    """Look it up in the store (if not found, a _Miss holding the key under which to store after expansion). A line in the bundle is looked up without opening
    the source or the entry if the source's stat is the same as when it was written."""
    if cache_dir is None:
        return _Miss(source, None)
    module = module_name(roots, source)
    relative = str(source.relative_to(root))
    stat = stat_of(source)  # taken before reading (even if it changes while reading, the next run reads it again as a mismatch)
    row = (bundle or {}).get(relative)
    if row is not None and row_is_current(row, module, stat):
        return _hit(source, relative, module, row.cached, row.place, stat)
    key = place(source.read_text(encoding="utf-8"), module, relative)
    if row is not None and row.place == key and row.module == module and record_is_current_here(row.cached.used):
        return _hit(source, relative, module, row.cached, key, stat)
    match load(cache_dir, key):
        case CachedProjection() as cached:
            if cached.imports is None:
                # An old entry that holds no import names — read it once and write them in, so later runs do not re-read the expansion text.
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
    """Store the expansion result (this is the single place that writes to the store — child processes do not write). A source whose expansion failed is not stored
    (it is rebuilt once fixed; the failure message is shown on every expansion). Return the bundle line for the stored expansion."""
    if cache_dir is not None and miss.key is not None and isinstance(result, Projection):
        cached = _to_cache(result)
        store(cache_dir, miss.key, cached)
        return BundleRow(miss.module, miss.key, miss.stat, cached)
    return None


def project_cached(
    root: Path, roots: list[Path], source: Path, cache_dir: Path | None
) -> Projection | CompileFailure:
    """Look in the expansion cache (static_cache), and if it is missing, expand and store. If cache_dir is None, expand every time."""
    match _looked_up(root, roots, source, cache_dir):
        case _Hit(projection=projection):
            return projection
        case _Miss() as miss:
            result = project(root, roots, source)
            _kept(cache_dir, miss, result)
            return result


def _project_in_child(root: Path, roots: list[Path], source: Path) -> Projection | CompileFailure:
    """Expand one source in a child process (a function handed to the pool — placed directly under the module so that the child can find it by import)."""
    return project(root, roots, source)


def _start_pool(workers: int) -> ProcessPoolExecutor:
    """Start the pool of child processes that run dependency expansion (forkserver — does not copy the parent's threads)."""
    return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("forkserver"))


class _Expanders:
    """Take charge of expanding dependencies not found in the store. Only in a stage with two or more unfound files are they run in parallel in the child process pool, and the pool is
    started the first time it is needed and reused across stages within one run (not started in a run where everything is found — so as not to add seconds when the store is effective)."""

    def __init__(self, limit: int) -> None:
        # os.process_cpu_count (the number of CPUs this process can use) exists from Python 3.13. Up to 3.12, substitute the host's CPU count.
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
        """Expand sources (one, or if there is only one child to run in parallel, in order in the parent). If a child crashes, name the files with no answer and stop."""
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
                "A child process in charge of dependency expansion crashed without returning an answer: " + ", ".join(unanswered)
            ) from error


def project_closure(
    root: Path,
    roots: list[Path],
    targets: list[Path],
    cache_dir: Path | None,
    limit: int,
) -> Closure:
    """Expand the checked files and all the .hy under the root that they import (if cache_dir is set, unchanged ones are read from it).

    Follow imports stage by stage: files within a stage do not use each other's expansion, so those found in the store are read in the parent, and
    those not found are handed together to _Expanders (limit = the upper bound on the number of parallel child processes; 1 means in order in the parent; there is no default —
    the caller decides it with expander_limit (the number the slot's authoritative source answered))."""
    projections: dict[Path, Projection] = {}
    failures: list[CompileFailure] = []
    # Whether a candidate path exists is looked up once per dir, as something that does not change during a run (stat-ing per candidate came to 100,000 times when checking 10 files).
    listing = functools.cache(_listing)
    resolved = functools.cache(_present_candidates)
    seen: set[Path] = set(targets)
    wave = list(dict.fromkeys(targets))
    # A store that bundles the whole closure into one file (a warm run reads just one bundle). Files that cannot be found via a bundle line are looked up the usual way.
    bundle_name = bundle_key(root, [str(t.relative_to(root)) for t in wave]) if cache_dir is not None else ""
    bundle = load_bundle(cache_dir, bundle_name) if cache_dir is not None else {}
    rows: dict[str, BundleRow] = {}
    # The expansion cache's check of "does it match the current environment" stats the state of files once per run (discarding the memo on every check
    # came to 100,000 stats and 10 seconds of CPU when checking 10 files).
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
                # The import's .hy is searched for in the import roots and in the parents from the importing file's dir to the root (pyright's search — search_dirs).
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
    """Within stage, make the dirs up to relative's parent real dirs, not symlinks (their contents are mirrored with symlinks)."""
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
        import tomllib  # Python 3.11 and later. On 3.10, [tool.pyright] in pyproject is not read

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
    """The position index of one expansion — so as not to re-split the whole text into lines per diagnostic and re-scan all ranges from the top (
    locate took 26 seconds on a file with 1,402 reds). lines = the lines of the expanded Python; covering = line number -> the numbers of the ranges that cover that line
    (in projection.spans' original order). A range is listed on every line from its starting line to its ending line (the ending line included)."""

    spans: Sequence[Span]
    lines: tuple[str, ...]
    covering: tuple[tuple[int, ...], ...]


def span_index(projection: Projection) -> SpanIndex:
    """Build the position index of one expansion exactly once (so that locate is called as many times as that expansion has diagnostics)."""
    rows = max((span.end[0] for span in projection.spans), default=-1) + 1
    # Sort the (line, range number) pairs by line and number, and group by line (number order = projection.spans' original order).
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
    """Map a position (0-based) in the expanded Python to the Hy position of the innermost node containing it.

    Every range that contains the point covers the point's line, so examine only the ranges covering that line in the original order (ranges that do not contain it are never chosen anyway, so
    the answer is the same as examining all ranges)."""
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
    """So that the python pyright launches to ask for the search path does not write .pyc: place in the directory a wrapper that launches python with `-B` (effective even under
    a launch that does not read the PYTHON* environment variables). pyright launches it in a way that ignores the caller's PYTHONDONTWRITEBYTECODE, so
    without the wrapper, the site startup writes the .pyc of modules the .pth reads into the checkout (the daily 2026-10-11 16:00 run of doeff)."""
    wrapper = directory / "python-without-bytecode"
    wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(python)} -B "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


#: Default number of pyright threads (pyright's --threads — splits the files to check across child processes and runs them in parallel). 1 = do not pass --threads
#: (one pyright). The daily type measurement (agora-controllers' hy-type-trend, 2,551 .hy files) took about 2,250 seconds with one, and did not fit within the processing stage's
#: limit (the 10-12 12:00 JST run). Each child reads the imports of its files to check separately, so total CPU and memory
#: grow with the count (on controllers/screen's 231 files, 2 = 0.7x seconds and 1.2x CPU; 4 = 0.5x seconds and 1.6x CPU).
#: So the default stays 1, and the caller raises it to fit the host through the environment variable PYRIGHT_THREADS_ENV (read by env_places; only the entry main accepts it).
PYRIGHT_THREADS = 1


def pyright_threads(configured: str | None, declared: int | None = None) -> int:
    """Decide the number of pyright threads (configured = the value of the environment variable PYRIGHT_THREADS_ENV; if absent, PYRIGHT_THREADS). A value that is not an integer of 1 or more
    is refused rather than silently dropped to the default (so the author's mistake is visible). A thread is a child process that pyright starts, so it does not exceed the number of parallel runs
    within one slot that the slot's authoritative source answered (declared; None if it did not arrive) — the environment variable is a way to lower the number, not to raise it beyond the declaration."""
    wanted = PYRIGHT_THREADS
    if configured is not None and configured.strip():
        text = configured.strip()
        if not text.isdigit() or int(text) < 1:
            raise ValueError(f"{PYRIGHT_THREADS_ENV} must be an integer of 1 or more: {configured!r}")
        wanted = int(text)
    return wanted if declared is None else min(wanted, declared)


def thread_clamp_note(configured: str | None, declared: int | None) -> str | None:
    """A one-line announcement for when the environment variable's setting is capped by the declared parallelism (pure; None if not capped). Capping used to discard the setting
    silently — even when the daily hy-type-trend set the env to 4, it ran with 2 every one of four times, and this stayed invisible until another conversation tracked down the cause
    (agora-controllers 93c94c6f9). The way the value is decided stays as in pyright_threads; this only makes it visible."""
    asked = pyright_threads(configured)
    used = pyright_threads(configured, declared)
    if used == asked:
        return None
    return (
        f"{PYRIGHT_THREADS_ENV}={asked} exceeds the parallelism {declared} declared for the host check slot, so pyright is run with {used} threads"
        " (a thread is a child process that pyright starts — there is no way to raise it beyond the declaration)"
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
                notes.append(f"{relative}: a .py with the same name exists, so {source.name} cannot be checked")
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
                f"cannot read pyright output (exit {completed.returncode}): "
                f"{completed.stdout[:400]}{completed.stderr[:400]}"
            ) from error
    diagnostics: list[Diagnostic] = []
    general = output.get("generalDiagnostics", []) if isinstance(output, dict) else []
    items = [
        item for item in (general if isinstance(general, list) else []) if isinstance(item, dict)
    ]
    # The position index is built exactly once per expansion that has diagnostics.
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


#: The functions that build pyright diagnostics and map them back to .hy positions (material for the carry fingerprint — if any of these sources changes, the previous run's diagnostics are not carried;
#: semantics_digest in doeff_hy/_static_carry.py). Settling (settle) is applied to carried diagnostics every time too, so it is not included.
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
    """To turn pyright's diagnostics into per-file carry lines (within a file, kept in pyright's answer order)."""
    ordered = sorted(enumerate(diagnostics), key=lambda pair: (pair[1].path, pair[0]))
    return tuple(
        FileRows(path, tuple(CarriedRow(d.line, d.column, d.severity, d.rule, d.message) for _, d in group))
        for path, group in groupby(ordered, key=lambda pair: pair[1].path)
    )


def carry_groups(sources: list[Path], group_size: int) -> list[list[Path]]:
    """Pure: to split the files to give pyright into groups of group_size (0 or less = one group; one group even when there is no file to check — so the note about a .py of the same name is emitted)."""
    if group_size <= 0 or len(sources) <= group_size:
        return [sources]
    return [sources[start : start + group_size] for start in range(0, len(sources), group_size)]


def _keep_carry(carry: Path, text: str) -> None:
    """To write the carry document (if it cannot be written, announce it and continue — the next run compares against the previous carry)."""
    try:
        write_carry(carry, text)
    except OSError as error:
        print(f"doeff-hy-check: cannot write carry file {carry} ({error}) — the next run compares against the previous carry", file=sys.stderr)


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
    group_size: int = 0,
) -> Checked:
    """To run pyright under the carry (doeff_hy/_static_carry.py): a .hy whose inputs are unchanged since the previous run is not checked, and the previous run's diagnostics for that file are
    carried over. Replace the file with this run's carry (if it cannot be written, announce it and continue — the next run compares against the previous carry), and print the number checked and the number carried to stderr
    in one line. If the carry file cannot be read, announce it and check everything (the carry is a shortcut for measurement, and must not stop the measurement). group_size = split the files to check into
    groups of this size and give them to pyright, writing an intermediate document per group (0 = do not split — see the head note "per-group rewriting" in _static_carry)."""
    try:
        previous: CarryRecord | CarryUnreadableError | None = read_carry(carry)
    except CarryUnreadableError as error:
        print(f"doeff-hy-check: {error} — checking everything", file=sys.stderr)
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
    groups = carry_groups(
        [source for source in targets if str(source.relative_to(root)) in chosen], group_size
    )
    diagnostics: list[Diagnostic] = []
    notes: list[str] = []
    done: set[str] = set()
    for index, group in enumerate(groups):
        part = run_pyright(root, settings, closure.projections, group, pyright, python, threads)
        diagnostics.extend(part.diagnostics)
        notes.extend(note for note in part.notes if note not in notes)  # the note about a .py of the same name comes out identical for every group
        done.update(str(source.relative_to(root)) for source in group)
        if index < len(groups) - 1:
            # The intermediate per-group document (head note "per-group rewriting" in _static_carry) — even if the user cuts it off by time, the groups already checked remain for the next run.
            partial, partial_summary = next_record(
                plan, environment.fingerprint, inputs, _rows_by_path(diagnostics), frozenset(done)
            )
            _keep_carry(carry, carry_text(partial, partial_summary))
    checked = Checked(diagnostics, notes)
    record, summary = next_record(plan, environment.fingerprint, inputs, _rows_by_path(checked.diagnostics))
    _keep_carry(carry, carry_text(record, summary))
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
    """To run pyright according to the entry arguments — under the carry if --carry is given (run_carried), otherwise over every named .hy (run_pyright)."""
    carry: Path | None = args.carry
    if carry is None:
        return run_pyright(root, settings, closure.projections, report, args.pyright, args.python, threads)
    return run_carried(
        root,
        roots,
        settings,
        closure,
        report,
        args.pyright,
        args.python,
        threads,
        carry,
        args.carry_refresh_runs,
        args.carry_group,
    )


def _hy_backed(module: str) -> bool:
    """Is the module's real body a .hy (pyright does not see a .hy as source, so only the stub is visible)."""
    relative = Path(*module.split("."))
    for entry in sys.path:
        base = Path(entry or ".") / relative
        if base.with_suffix(".hy").is_file() or (base / "__init__.hy").is_file():
            return True
    return False


_GUARD_MESSAGE = (
    "A Program / effect placed in statement position does not run — bind it with (<- _ ...) or make it the last expression of the body"
    " [ADR-DOE-HY-001]"
)
_UNKNOWN_TYPES = frozenset({"Unknown", "Any"})
#: The helper names that the type-checking expansion places at the head of a module (read from static_view.STATIC_HELPER_IMPORTS — so the sequence of names is not held in two places).
_STATIC_HELPER_NAMES: frozenset[str] = frozenset(
    _bound_name(alias)
    for statement in ast.parse(STATIC_HELPER_IMPORTS).body
    if isinstance(statement, ast.ImportFrom)
    for alias in statement.names
)


def _revealed(diagnostic: Diagnostic) -> str | None:
    """If it is the `reveal_type` information (`Type of "x" is "T"`), T."""
    if diagnostic.severity != "information" or not diagnostic.message.startswith("Type of "):
        return None
    quoted = diagnostic.message.rsplit('"', 2)
    return quoted[-2] if len(quoted) == 3 else None


def settle(diagnostics: list[Diagnostic]) -> Checked:
    """Tidy up what comes out of the expansion's circumstances. Returns (diagnostics, notes).

    - Collapse the same diagnostic into one: when a macro copies the same user expression to two places, the red inside that expression
      comes out twice at the same Hy position.
    - A module whose real body is a .hy: pyright does not see a .hy as source, so drop the "source not found"
      warning, and turn the "cannot be resolved" red into a note (the types of that module are not visible).
    - A Program / effect in statement position (the deprecated overload in macros.pyi): drop it if the `reveal_type` at the same position
      is Unknown / Any (do not assert on a value whose type is unknown).
      Replace the wording of what is kept. The `reveal_type` information itself is not emitted.
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
        # Helper names (_doeff_do, _doeff_perform and others) are names doeff-hy places in the expansion, and users do not write them — strict's
        # "private name used outside its module" is an artifact of the expansion, so do not emit it.
        if diagnostic.rule == "reportPrivateUsage" and module in _STATIC_HELPER_NAMES:
            continue
        if diagnostic.rule == "reportMissingImports" and _hy_backed(module):
            notes.append(f"{diagnostic.path}:{diagnostic.line}: {module} is a .hy, so its types are not visible")
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
                    f"{_GUARD_MESSAGE} (type of the value: {revealed[key]})",
                )
            )
            continue
        kept.append(diagnostic)
    return Checked(kept, list(dict.fromkeys(notes)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doeff-hy-check", description="Expand Hy source and check types with pyright"
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
        help="add an import root under the root (repeatable; also added to pyright's extraPaths)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="baseline red file (what --write-baseline wrote). Only reds absent from the baseline make the exit code 1",
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        help="write this run's reds out as a baseline file (exit 0 when written)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="location of the expansion cache (default = $XDG_CACHE_HOME/doeff-hy-check or ~/.cache/doeff-hy-check)",
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="do not use the expansion cache; expand everything every time"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="set pyright's typeCheckingMode to strict (takes precedence over the root's config)",
    )
    parser.add_argument(
        "--carry",
        type=Path,
        default=None,
        help="carry file (the previous run's diagnostics and the digest of inputs). A .hy with unchanged inputs is not checked and the previous run's diagnostics are carried over, and the file is replaced with this run's",
    )
    parser.add_argument(
        "--carry-refresh-runs",
        type=int,
        default=DEFAULT_REFRESH_RUNS,
        help="re-check even a .hy with unchanged inputs once within this many runs (0 = never re-check)",
    )
    parser.add_argument(
        "--carry-group",
        type=int,
        default=0,
        help="split the .hy files to check into groups of this size, give them to pyright, and rewrite the carry file per group (the groups already checked remain even if cut off; 0 = do not split)",
    )
    args = parser.parse_args(argv)
    try:
        baseline = read_baseline(args.baseline) if args.baseline else None
    except BaselineUnreadable as error:
        print(f"doeff-hy-check: could not run: {error}", file=sys.stderr)
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
    # Map the named paths to the root's spelling. A path outside the root is refused, not silently dropped — dropping it would answer 0 reds for a file that has reds.
    named = [(p, in_root(root, p if p.is_absolute() else Path.cwd() / p)) for p in (args.paths or [root])]
    outside = [str(p) for p, placed in named if placed is None]
    if outside:
        print(f"doeff-hy-check: could not run: a named path is outside the root (root = {root}): {', '.join(outside)}", file=sys.stderr)
        return 2
    report = hy_files([placed for _, placed in named if placed is not None])
    # Hold one slot of the host check slot between expansion and pyright — taken here regardless of the route that launches it (a conversation directly, a gate child, daily, inside pytest) (doeff_hy._check_slot).
    slot = take_slot(Path.home(), slot_label(root, conversation_setting()), sys.stderr)
    if isinstance(slot, SlotExpired):
        print(f"doeff-hy-check: could not run: {slot.line}", file=sys.stderr)
        return 2
    try:
        cache_dir = None if args.no_cache else (args.cache_dir or default_cache_dir())
        threads = pyright_threads(pyright_threads_setting(), slot.width)  # decide before expansion (so as not to spend expansion seconds on a wrong value)
        if (clamped := thread_clamp_note(pyright_threads_setting(), slot.width)) is not None:
            print(f"doeff-hy-check: {clamped}", file=sys.stderr)
        closure = project_closure(root, roots, report, cache_dir, expander_limit(slot.width))
        checked = _checked(root, roots, settings, closure, report, args, threads)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"doeff-hy-check: could not run: {error}", file=sys.stderr)
        return 2
    finally:
        slot.release()
    compile_errors = [f.diagnostic for f in closure.failures if f.source in report]
    # Findings of the val / var checks (use of setv = warning, rebinding = red, old lazy = warning — ADR-DOE-HY-006).
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
            f"doeff-hy-check: wrote the baseline to {args.write_baseline} ({len(errors_of(diagnostics))} errors)",
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
            f"Checked {len(report)} .hy files: {len(verdict.new)} new errors, "
            f"{len(verdict.known)} errors in the baseline, {warnings} warnings",
            file=sys.stderr,
        )
    return 1 if verdict.new else 0


if __name__ == "__main__":
    raise SystemExit(main())
