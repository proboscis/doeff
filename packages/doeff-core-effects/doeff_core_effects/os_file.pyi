# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = os_file.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from pathlib import Path as Path
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.file_effects import DirEntry as DirEntry
from doeff_core_effects.file_effects import LockHeld as LockHeld
from doeff_core_effects.file_effects import DiskUsage as DiskUsage
from doeff_core_effects.file_effects import StatPath as StatPath
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import ReadBytes as ReadBytes
from doeff_core_effects.file_effects import WriteText as WriteText
from doeff_core_effects.file_effects import WriteBytes as WriteBytes
from doeff_core_effects.file_effects import AppendText as AppendText
from doeff_core_effects.file_effects import MakeDirectory as MakeDirectory
from doeff_core_effects.file_effects import ListDirectory as ListDirectory
from doeff_core_effects.file_effects import WalkTree as WalkTree
from doeff_core_effects.file_effects import CopyFile as CopyFile
from doeff_core_effects.file_effects import CopyTree as CopyTree
from doeff_core_effects.file_effects import RenamePath as RenamePath
from doeff_core_effects.file_effects import RemoveTree as RemoveTree
from doeff_core_effects.file_effects import AcquireLock as AcquireLock
from doeff_core_effects.file_effects import ReleaseLock as ReleaseLock
from doeff_core_effects.file_effects import ReadDiskFree as ReadDiskFree
from doeff_core_effects.file_effects import ReadDiskUsage as ReadDiskUsage
from doeff_core_effects.file_effects import MeasureTree as MeasureTree
from doeff_core_effects.file_effects import LinkFile as LinkFile
from doeff_core_effects.file_effects import CompilePythonSources as CompilePythonSources
from doeff_core_effects.python_bytecode import compile_python_sources as compile_python_sources
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def failed(path: str, error: OSError) -> _Program[FileFailed, object]:
    ...

def kind_of_mode(mode: int) -> _Program[PathKind, object]:
    ...

def stat_path(path: str, follow_symlinks: bool) -> _Program[PathStat | FileFailed, object]:
    ...

def write_content(path: str, content: str | bytes, mode: int | None, replace: bool, sync: bool) -> _Program[FileFailed | None, object]:
    ...

def make_directory(path: str, mode: int | None) -> _Program[FileFailed | None, object]:
    ...

def entry_of(path: str, name: str) -> _Program[DirEntry, object]:
    ...

def list_directory(path: str) -> _Program[tuple[DirEntry, ...] | FileFailed, object]:
    ...

def walk_tree(path: str) -> _Program[tuple[DirEntry, ...] | FileFailed, object]:
    ...

def remove_tree(path: str) -> _Program[FileFailed | None, object]:
    ...

def acquire_lock(path: str) -> _Program[LockHeld | FileFailed, object]:
    ...

def guarded(path: str, action: Callable[[], object]) -> _Program[FileFailed | None, object]:
    ...

def read_file(path: str, binary: bool, limit: int | None) -> _Program[str | bytes | FileFailed, object]:
    ...

def _sync(handle: Incomplete) -> Incomplete:
    ...

def _append(path: Incomplete, text: Incomplete, sync: Incomplete) -> Incomplete:
    ...

def _release(held: Incomplete) -> Incomplete:
    ...

def disk_free(path: str) -> _Program[int | FileFailed, object]:
    ...

def disk_usage(path: str) -> _Program[DiskUsage | FileFailed, object]:
    ...

def measure_tree(path: str) -> _Program[int | FileFailed, object]:
    ...
os_file_handler: _Handler
