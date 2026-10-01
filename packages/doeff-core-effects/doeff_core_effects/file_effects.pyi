"""file_effects.hy の公開面の型(型検査のための宣言 — 実行時は file_effects.hy を読む・agora-redesign #2323)。

file_effects.hy は Hy の module なので、型の宣言が無いと pyright は中を読めず、`from doeff_core_effects.file_effects import ReadText`
の名が全部 Unknown になる(消費者の strict の型検査で、書き手に直せない赤が連なる)。ここで型を宣言する。

- defrecord は frozen で keyword だけの dataclass、defenum は StrEnum(値は名の小文字)。
- effect(`(defclass [(dataclass :frozen True)] … [EffectBase])`)は位置でも渡せる frozen の dataclass で、`EffectBase[答えの型]` の
  下位の型。失敗は値 FileFailed で答えるので、本物の file system に触れる effect の答えは成功の答えと FileFailed の和
  (.hy の頭の註と os_file.hy の各 defk の :post のとおり)。ReadMemoryFiles は memory の置き場の中身だけを答える。
- defk は呼ぶと Program を返す(答えの型は Program の 1 つ目の引数)。file-done は答えから FileFailed を除いた型を返す。
- 実装との食い違いは packages/doeff-core-effects/tests/test_hy_module_stubs.py が名・欄の名と順・既定値の有無・引数の名で検める。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from doeff_vm import EffectBase

from doeff import Program

_A = TypeVar("_A")

class PathKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    MISSING = "missing"
    OTHER = "other"

# --- 値 ---

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
class LockHeld:
    path: str
    token: int

# --- effect ---

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

# --- memory の置き場の語彙 ---

@dataclass(frozen=True, kw_only=True)
class MemoryFile:
    path: str
    content: bytes
    mode: int | None = None

@dataclass(frozen=True, kw_only=True)
class MemoryFiles:
    files: tuple[MemoryFile, ...] = ()
    dirs: tuple[str, ...] = ()
    locks: tuple[str, ...] = ()
    free: int = ...

def file_done(request: EffectBase[_A | FileFailed]) -> Program[_A, Any]: ...
@dataclass(frozen=True)
class ReadMemoryFiles(EffectBase[MemoryFiles]): ...
