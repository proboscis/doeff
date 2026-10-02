# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = file_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff import EffectBase as EffectBase

class PathKind(StrEnum):
    FILE = 'file'
    DIRECTORY = 'directory'
    SYMLINK = 'symlink'
    MISSING = 'missing'
    OTHER = 'other'

@dataclass(frozen=True, kw_only=True)
class FileFailed:
    path: str
    detail: str

@dataclass(frozen=True, kw_only=True)
class PathStat:
    kind: PathKind
    real_path: str
    size: int
    modified: float

@dataclass(frozen=True, kw_only=True)
class DirEntry:
    name: str
    kind: PathKind

@dataclass(frozen=True, kw_only=True)
class DiskUsage:
    total: int
    free: int

@dataclass(frozen=True, kw_only=True)
class SourceNotCompiled:
    path: str
    reason: str

@dataclass(frozen=True, kw_only=True)
class LockHeld:
    path: str
    token: int

@dataclass(frozen=True)
class StatPath(EffectBase[PathStat | FileFailed]):
    path: str
    follow_symlinks: bool = True

@dataclass(frozen=True)
class ReadText(EffectBase[str | FileFailed]):
    path: str

@dataclass(frozen=True)
class ReadBytes(EffectBase[bytes | FileFailed]):
    path: str
    limit: int | None = None

@dataclass(frozen=True)
class WriteText(EffectBase[FileFailed | None]):
    path: str
    text: str
    mode: int | None = None
    replace: bool = False
    sync: bool = False

@dataclass(frozen=True)
class WriteBytes(EffectBase[FileFailed | None]):
    path: str
    content: bytes
    mode: int | None = None
    replace: bool = False
    sync: bool = False

@dataclass(frozen=True)
class AppendText(EffectBase[FileFailed | None]):
    path: str
    text: str
    sync: bool = False

@dataclass(frozen=True)
class MakeDirectory(EffectBase[FileFailed | None]):
    path: str
    mode: int | None = None

@dataclass(frozen=True)
class ListDirectory(EffectBase[tuple[DirEntry, ...] | FileFailed]):
    path: str

@dataclass(frozen=True)
class WalkTree(EffectBase[tuple[DirEntry, ...] | FileFailed]):
    path: str

@dataclass(frozen=True)
class CopyFile(EffectBase[FileFailed | None]):
    source: str
    target: str

@dataclass(frozen=True)
class CompilePythonSources(EffectBase[tuple[SourceNotCompiled, ...]]):
    tree: str
    items: tuple[tuple[str, str], ...]
    jobs: int = 1
    roots: tuple[str, ...] = ...

@dataclass(frozen=True)
class LinkFile(EffectBase[FileFailed | None]):
    source: str
    target: str

@dataclass(frozen=True)
class CopyTree(EffectBase[FileFailed | None]):
    source: str
    target: str

@dataclass(frozen=True)
class RenamePath(EffectBase[FileFailed | None]):
    source: str
    target: str

@dataclass(frozen=True)
class RemoveTree(EffectBase[FileFailed | None]):
    path: str

@dataclass(frozen=True)
class AcquireLock(EffectBase[LockHeld | FileFailed]):
    path: str

@dataclass(frozen=True)
class ReleaseLock(EffectBase[FileFailed | None]):
    held: LockHeld

@dataclass(frozen=True)
class ReadDiskFree(EffectBase[int | FileFailed]):
    path: str

@dataclass(frozen=True)
class ReadDiskUsage(EffectBase[DiskUsage | FileFailed]):
    path: str

@dataclass(frozen=True)
class MeasureTree(EffectBase[int | FileFailed]):
    path: str

@dataclass(frozen=True, kw_only=True)
class MemoryFile:
    path: str
    content: bytes
    mode: int | None = None

@dataclass(frozen=True, kw_only=True)
class MemoryFiles:
    files: tuple[MemoryFile, ...] = ...
    dirs: tuple[str, ...] = ...
    locks: tuple[str, ...] = ...
    free: int = ...
    total: int = ...

def file_done[A](request: EffectBase[A | FileFailed]) -> _Program[A, object]:
    ...

@dataclass(frozen=True)
class ReadMemoryFiles(EffectBase[MemoryFiles]):
    ...
