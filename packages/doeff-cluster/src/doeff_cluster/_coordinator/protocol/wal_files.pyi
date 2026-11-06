# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = wal_files.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_core_effects import slog as slog
from doeff_core_effects import Appended as Appended
from doeff_core_effects import AppendBytes as AppendBytes
from doeff_core_effects import MakeDirectory as MakeDirectory
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import PathStat as PathStat
from doeff_core_effects import ReadBytes as ReadBytes
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import StatPath as StatPath
from doeff_core_effects import SyncDirectory as SyncDirectory
from doeff_core_effects import TruncateFile as TruncateFile
from doeff_core_effects import WriteBytes as WriteBytes
from doeff_core_effects._file_effects import file_done as file_done
from doeff_cluster._shared.core.clock import now_epoch_ms as now_epoch_ms
MOVED_MARK: str
SLOW_FSYNC_SECONDS: float
FSYNC_REPORT_EVERY: int
SNAPSHOT_MODE: int

def wal_check_place(store: Incomplete) -> _Program[None, object]:
    ...

def wal_exists(store: Incomplete) -> _Program[bool, object]:
    ...

def wal_read_snapshot(store: Incomplete) -> _Program[bytes | None, object]:
    ...

def log_lines_of(data: bytes) -> _Program[list[bytes], object]:
    ...

def wal_read_log_lines(store: Incomplete) -> _Program[list[bytes], object]:
    ...

def wal_drop_tail(store: Incomplete, kept: int, size: int, reason: str) -> _Program[None, object]:
    ...

def wal_append_line(store: Incomplete, line: bytes) -> _Program[int, object]:
    ...

def wal_write_snapshot(store: Incomplete, data: bytes) -> _Program[None, object]:
    ...
