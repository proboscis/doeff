# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = memory_file.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import replace as with_fields
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.file_effects import DirEntry as DirEntry
from doeff_core_effects.file_effects import LockHeld as LockHeld
from doeff_core_effects.file_effects import MemoryFile as MemoryFile
from doeff_core_effects.file_effects import MemoryLink as MemoryLink
from doeff_core_effects.file_effects import MemoryFiles as MemoryFiles
from doeff_core_effects.file_effects import ReadMemoryFiles as ReadMemoryFiles
from doeff_core_effects.file_effects import StatPath as StatPath
from doeff_core_effects.file_effects import MakeSymlink as MakeSymlink
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
from doeff_core_effects.file_effects import DiskUsage as DiskUsage
from doeff_core_effects.file_effects import ReadDiskUsage as ReadDiskUsage
from doeff_core_effects.file_effects import MeasureTree as MeasureTree
from doeff_core_effects.file_effects import LinkFile as LinkFile
from doeff_core_effects.file_effects import CompilePythonSources as CompilePythonSources
from doeff_core_effects.file_effects import SourceNotCompiled as SourceNotCompiled
from doeff_core_effects.python_bytecode import compiled_pyc as compiled_pyc
from doeff_core_effects.python_bytecode import kept_pyc as kept_pyc
from doeff_core_effects.python_bytecode import pyc_path as pyc_path
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
ROOT: str
NO_ENTRY: str
NOT_DIRECTORY: str
IS_DIRECTORY: str
EXISTS: str
NOT_EMPTY: str
INVALID: str
NOT_PERMITTED: str
LOOP: str
MAX_HOPS: int
ANSWER: TypeAlias = FileFailed | PathStat | LockHeld | MemoryFiles | str | bytes | tuple | int | None

def refused(reason: str, path: str) -> _Program[FileFailed, object]:
    ...

def refused_move(reason: str, source: str, target: str) -> _Program[FileFailed, object]:
    ...

def normal(path: str) -> _Program[str, object]:
    ...

def kind_in(store: MemoryFiles, path: str) -> _Program[PathKind, object]:
    ...

def link_at(store: MemoryFiles, path: str) -> _Program[MemoryLink | None, object]:
    ...

def resolved(store: MemoryFiles, path: str, follow_last: bool) -> _Program[str | FileFailed, object]:
    ...

def as_asked(answer: ANSWER, asked: str, at: str) -> _Program[ANSWER, object]:
    ...

def hyx_underXquestion_markX(path: str, root: str) -> Incomplete:
    ...

def parent_refusal(store: MemoryFiles, path: str) -> _Program[FileFailed | None, object]:
    ...

def with_dirs(store: MemoryFiles, path: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def with_file(store: MemoryFiles, path: str, content: bytes, mode: int | None) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def symlink_in(store: MemoryFiles, path: str, target: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def link_in(store: MemoryFiles, source: str, target: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def compile_in_store(store: MemoryFiles, tree: str, items: tuple[tuple[str, str], ...]) -> _Program[tuple[MemoryFiles, tuple[SourceNotCompiled, ...]], object]:
    ...

def content_of(store: MemoryFiles, path: str) -> _Program[bytes | FileFailed, object]:
    ...

def dir_refusal(store: MemoryFiles, path: str) -> _Program[FileFailed | None, object]:
    ...

def entries_below(store: MemoryFiles, path: str) -> _Program[tuple[DirEntry, ...], object]:
    ...

def list_in(store: MemoryFiles, path: str) -> _Program[tuple[DirEntry, ...] | FileFailed, object]:
    ...

def without_tree(store: MemoryFiles, path: str) -> _Program[MemoryFiles, object]:
    ...

def remove_in(store: MemoryFiles, path: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def copy_tree_in(store: MemoryFiles, source: str, target: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def moved(path: str, source: str, target: str) -> _Program[str, object]:
    ...

def rename_in(store: MemoryFiles, source: str, target: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def return_path(path: str) -> _Program[str, object]:
    ...

def stat_in(store: MemoryFiles, path: str) -> _Program[PathStat, object]:
    ...

def memory_file_handler(initial: MemoryFiles) -> _Handler:
    ...

def on_path(store: MemoryFiles, path: str, follow_last: bool, op: Callable) -> _Program[ANSWER, object]:
    ...

def on_pair(store: MemoryFiles, source: str, target: str, follow_last: bool, op: Callable) -> _Program[ANSWER, object]:
    ...

def appended_in(store: MemoryFiles, path: str, text: str) -> _Program[MemoryFiles | FileFailed, object]:
    ...

def walk_in(store: MemoryFiles, path: str) -> _Program[tuple[DirEntry, ...] | FileFailed, object]:
    ...

def measured_in(store: MemoryFiles, path: str) -> _Program[int | FileFailed, object]:
    ...

def return_store(store: MemoryFiles) -> _Program[MemoryFiles, object]:
    ...
