# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = memory_watch_world.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from doeff import Program as Program
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ProcRow as ProcRow
from doeff_core_effects import ReadProcTable as ReadProcTable
from doeff_core_effects import SignalPid as SignalPid
from doeff_cluster._worker.core.memory_rules import JobRoot as JobRoot
from doeff_cluster._worker.core.memory_rules import MIB as MIB
from doeff_cluster._worker.protocol.observations import ObserveJobRoots as ObserveJobRoots
from doeff_cluster._worker.protocol.memory_watch import MemoryWatchSettings as MemoryWatchSettings
FLOOD_BASE_PID: int

@dataclass(frozen=True, kw_only=True)
class SimProcFlood:
    job: str
    children: int
    child_rss_bytes: int = ...
    comm: str = 'python'

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class SimMemoryWatch:
    floods: tuple = ...
    sample_seconds: float = 5.0
    cpu_limit_seconds: float = 10.0
    stall_floor_seconds: float = 1.0
    unfinished_stall_seconds: float = 3600.0

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class SimMemoryRead:
    worker: str
    boot: str
    at: int
    rows: int
    cpu_seconds: float
    finished: bool
    stalled_ms: int

class ReadOverLimit(BaseException):
    ...

def sim_watch_settings(watch: SimMemoryWatch) -> _Program[MemoryWatchSettings, object]:
    ...

def sim_proc_rows(roots: tuple, task_rss_bytes: int, floods: tuple) -> _Program[tuple, object]:
    ...

def sim_meminfo_text(total_mib: int, available_mib: int) -> _Program[str, object]:
    ...

def sim_watch_answers(settings: MemoryWatchSettings, meminfo: str, roots: tuple, rows: tuple) -> _Handler:
    ...

def limited_cpu_seconds(body: Program, cpu_limit_seconds: float) -> _Program[tuple, object]:
    ...
