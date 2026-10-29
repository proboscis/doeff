# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = code_prepare.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from concurrent.futures import ThreadPoolExecutor as ThreadPoolExecutor
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from types import ModuleType as ModuleType
from doeff import EffectBase as EffectBase
from doeff import run as run
from doeff import with_handlers as with_handlers
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import sync_time_handler as sync_time_handler
from doeff_core_effects import slog_handler as slog_handler
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import PathStat as PathStat
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import StatPath as StatPath
from doeff_core_effects._file_effects import file_done as file_done
from doeff_core_effects import os_file_handler as os_file_handler
from doeff_core_effects.os_process import subprocess_handler as subprocess_handler
from doeff_core_effects.process_effects import InterpreterFacts as InterpreterFacts
from doeff_core_effects.process_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects.process_effects import ReadInterpreter as ReadInterpreter
from doeff_core_effects.process_effects import RunProcess as RunProcess
from doeff_cluster.worker.core.code_plan import compile_plan as compile_plan
from doeff_cluster.worker.core.code_plan import marker_content as marker_content
from doeff_cluster.worker.core.code_plan import tree_problem as tree_problem
from doeff_cluster.worker.intent.code_model import ScanTree as ScanTree
from doeff_cluster.worker.intent.code_model import WriteMarker as WriteMarker
from doeff_cluster.worker.intent.code_model import Note as Note
from doeff_cluster.worker.protocol.tree_files import tree_files as tree_files
WORKER_CODE: Incomplete
BAKE_PLAN: str
BAKE_PLAN_MODULE: str
POOL_TOOL: str
CODE_STORE: str
CODE_STORE_MODULE: str

def module_at(name: str, path: str) -> _Program[ModuleType, object]:
    ...
plan: Incomplete
store: Incomplete

def usable_cpus() -> _Program[int, object]:
    ...

@dataclass(frozen=True)
class BakeSources(EffectBase):
    items: tuple
    jobs: int
    paths: tuple
    code_store: str | None

@dataclass(frozen=True)
class WriteStoreEntry(EffectBase):
    path: str
    data: bytes

@dataclass(frozen=True)
class DiscardStoreEntry(EffectBase):
    path: str
    problem: str

@dataclass(frozen=True)
class ReadSources(EffectBase):
    paths: tuple

@dataclass(frozen=True)
class ReadStoreEntries(EffectBase):
    paths: tuple

@dataclass(frozen=True)
class MarkProgress(EffectBase):
    ...

def bake_trees(shaped: tuple) -> _Program[tuple, object]:
    ...

def imports_entry_path(code_store: str | None, rel: str, text: str, hy_version: str) -> _Program[str | None, object]:
    ...

def stored_imports(path: str | None, data: bytes | None) -> _Program[tuple | None, object]:
    ...

def store_imports(code_store: str | None, rel: str, text: str, hy_version: str, imports: tuple) -> _Program[str | None, object]:
    ...

def closure_of_trees(trees: tuple, sources: tuple, entries: tuple, code_store: str | None, hy_version: str) -> _Program[tuple, object]:
    ...

def prepare_trees(trees: tuple, revision: str, jobs: int, entries: tuple, code_store: str | None, hy_version: str) -> _Program[tuple, object]:
    ...

def pool_tool_baker(progress: Incomplete) -> _Handler:
    ...
store_entries: _Handler
READ_THREADS: int

def read_source(path: str) -> str | None:
    ...

def read_entry_or_none(path: str | None) -> bytes | None:
    ...

def read_all(reader: Incomplete, paths: tuple) -> _Program[tuple, object]:
    ...
closure_reads: _Handler

def progress_marks(path: Incomplete) -> _Handler:
    ...

def main() -> None:
    ...
