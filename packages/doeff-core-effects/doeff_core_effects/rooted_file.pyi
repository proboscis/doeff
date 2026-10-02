# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = rooted_file.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import posixpath as posixpath
from dataclasses import replace as replace
from doeff import EffectBase as EffectBase
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import PathStat as PathStat
from doeff_core_effects.file_effects import LockHeld as LockHeld
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
from doeff_core_effects.file_effects import DiskUsage as DiskUsage
from doeff_core_effects.file_effects import ReadDiskUsage as ReadDiskUsage
from doeff_core_effects.file_effects import MeasureTree as MeasureTree
from doeff_core_effects.file_effects import LinkFile as LinkFile
from doeff_core_effects.file_effects import CompilePythonSources as CompilePythonSources
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
PATH_EFFECTS: Incomplete
MOVE_EFFECTS: Incomplete
ANSWER: TypeAlias = FileFailed | PathStat | LockHeld | DiskUsage | str | bytes | tuple | int | None

def outer_path(root: str, path: str) -> _Program[str, object]:
    ...

def inner_path(root: str, path: str) -> _Program[str, object]:
    ...

def inner_detail(root: str, detail: str) -> _Program[str, object]:
    ...

def inner_answer(root: str, answer: ANSWER) -> _Program[ANSWER, object]:
    ...

def rooted_file_handler(root: str) -> _Handler:
    ...

def moved(root: str, request: MOVE_EFFECTS) -> _Program[ANSWER, object]:
    ...
