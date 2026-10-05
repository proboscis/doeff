# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = bake_plan.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_cluster.worker.core.code_plan import carry_pairs as carry_pairs
from doeff_cluster.worker.core.code_plan import imported_names as imported_names
from doeff_cluster.worker.core.code_plan import module_name as module_name

@dataclass(frozen=True, kw_only=True)
class TreeArgs:
    named: str
    roots: tuple
    old: str | None
    changed: str | None

@dataclass(frozen=True, kw_only=True)
class BakeTree:
    named: str
    path: str
    roots: tuple
    old: str | None
    changed: frozenset

@dataclass(frozen=True, kw_only=True)
class ModuleIndex:
    names: tuple
    places: tuple

@dataclass(frozen=True, kw_only=True)
class ImportRow:
    rel: str
    digest: str
    imports: tuple

@dataclass(frozen=True, kw_only=True)
class ImportTable:
    rows: tuple
    problem: str | None

class PycScheme(StrEnum):
    CHECKED_HASH = 'checked-hash'
    UNCHECKED_HASH = 'unchecked-hash'
    TIMESTAMP = 'timestamp'
    UNREADABLE = 'unreadable'

@dataclass(frozen=True, kw_only=True)
class PycHead:
    path: str
    scheme: PycScheme
    magic: bytes

@dataclass(frozen=True, kw_only=True)
class BakeItem:
    tree: str
    rel: str
    name: str
    size: int

@dataclass(frozen=True, kw_only=True)
class TreeOutcome:
    named: str
    carried: int
    rebuilt: int
    reused: int
    failed: int
    problem: str | None

@dataclass(frozen=True, kw_only=True)
class BakeAnswer:
    failed: tuple
    reused: tuple

@dataclass(frozen=True, kw_only=True)
class BakeSummary:
    trees: tuple
    scan_s: float
    closure_s: float
    carry_s: float
    compile_s: float

def cpu_limit_of(cpu_max: str | None, available: int) -> _Program[int, object]:
    ...

def tree_arguments(trees: tuple, roots: tuple, olds: tuple, changes: tuple) -> _Program[tuple | str, object]:
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
IMPORT_TABLE: str
IMPORT_TABLE_FORMAT: int

def source_digest(text: str) -> _Program[str, object]:
    ...

def import_row_of(rel: str, value: dict | list | str | int | float | bool | None) -> _Program[ImportRow | None, object]:
    ...

def import_table_of(text: str | None) -> _Program[ImportTable, object]:
    ...

def usable_row(table: ImportTable, rel: str, digest: str, changed: frozenset) -> _Program[ImportRow | None, object]:
    ...

def import_table_json(rows: tuple) -> _Program[dict, object]:
    ...

def import_table_text(rows: tuple) -> _Program[str, object]:
    ...

def scoped_sources(sources: list | tuple, scope: frozenset | None) -> _Program[list, object]:
    ...

def tree_failures(failures: tuple, tree: str) -> _Program[list, object]:
    ...

def tree_reused(reused: tuple, tree: str) -> _Program[int, object]:
    ...
PYC_HEAD_BYTES: int

def pyc_head_of(path: str, head: bytes | None) -> _Program[PycHead, object]:
    ...

def carried_pycs(heads: tuple, magic: bytes, old_sources: frozenset, new_sources: frozenset, new_pycs: frozenset, changed: frozenset) -> _Program[list, object]:
    ...

def bake_order(items: tuple) -> _Program[tuple, object]:
    ...

def bake_argv(python: str, tool: str, jobs: int, paths: tuple) -> _Program[tuple, object]:
    ...

def bake_input(items: tuple) -> _Program[str, object]:
    ...

def bake_answer(text: str) -> _Program[BakeAnswer, object]:
    ...

def tree_line(outcome: TreeOutcome) -> _Program[str, object]:
    ...

def total_line(summary: BakeSummary) -> _Program[str, object]:
    ...
