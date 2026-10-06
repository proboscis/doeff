# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = bake_plan.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.worker.core.code_plan import imported_names as imported_names
from doeff_cluster.worker.core.code_plan import module_name as module_name

@dataclass(frozen=True, kw_only=True)
class TreeArgs:
    named: str
    roots: tuple

@dataclass(frozen=True, kw_only=True)
class BakeTree:
    named: str
    path: str
    roots: tuple

@dataclass(frozen=True, kw_only=True)
class ModuleIndex:
    names: tuple
    places: tuple

@dataclass(frozen=True, kw_only=True)
class BakeItem:
    tree: str
    rel: str
    name: str
    size: int

@dataclass(frozen=True, kw_only=True)
class TreeOutcome:
    named: str
    stored: int
    rebuilt: int
    reused: int
    failed: int
    problem: str | None

@dataclass(frozen=True, kw_only=True)
class BakeAnswer:
    failed: tuple
    stored: tuple
    reused: tuple
    unstored: tuple

@dataclass(frozen=True, kw_only=True)
class BakeSummary:
    trees: tuple
    scan_s: float
    closure_s: float
    compile_s: float

def cpu_limit_of(cpu_max: str | None, available: int) -> _Program[int, object]:
    ...

def tree_arguments(trees: tuple, roots: tuple) -> _Program[tuple | str, object]:
    ...

def trees_import_path(trees: tuple) -> _Program[tuple, object]:
    ...

def module_index(trees: tuple, sources: tuple) -> _Program[ModuleIndex, object]:
    ...

def closure_step(index: ModuleIndex, frontier: frozenset, seen: frozenset) -> _Program[tuple, object]:
    ...

def module_place(index: ModuleIndex, name: str) -> _Program[tuple, object]:
    ...

def imported_modules(index: ModuleIndex, found: tuple, imports: tuple) -> _Program[frozenset, object]:
    ...

def closure_scopes(index: ModuleIndex, seen: frozenset, count: int) -> _Program[tuple, object]:
    ...
IMPORTS_TAG: str

def imports_key_parts(rel: str, hy_version: str) -> _Program[tuple, object]:
    ...

def imports_entry(imports: tuple) -> _Program[bytes, object]:
    ...

def imports_of_entry(data: bytes) -> _Program[tuple | str, object]:
    ...

def scoped_sources(sources: list | tuple, scope: frozenset | None) -> _Program[list, object]:
    ...

def tree_failures(failures: tuple, tree: str) -> _Program[list, object]:
    ...

def tree_count(listed: tuple, tree: str) -> _Program[int, object]:
    ...

def bake_order(items: tuple) -> _Program[tuple, object]:
    ...

def bake_argv(python: str, tool: str, jobs: int, paths: tuple, store_module: str, store: str | None) -> _Program[tuple, object]:
    ...

def bake_input(items: tuple) -> _Program[str, object]:
    ...

def bake_answer(text: str) -> _Program[BakeAnswer, object]:
    ...

def tree_line(outcome: TreeOutcome) -> _Program[str, object]:
    ...

def total_line(summary: BakeSummary) -> _Program[str, object]:
    ...
