# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = load_rules.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster._worker.core.memory_rules import MIB as MIB
from doeff_cluster._worker.core.memory_rules import NodeMemory as NodeMemory
from doeff_cluster._worker.core.memory_rules import JobUse as JobUse
from doeff_cluster._worker.core.memory_rules import TASK_PREFIX as TASK_PREFIX
from doeff_cluster._worker.core.memory_rules import node_memory_of as node_memory_of
from doeff_cluster._worker.core.eviction_rules import EvictionLine as EvictionLine
from doeff_cluster._worker.core.eviction_rules import line_memory as line_memory
LOAD_READ_EVERY_MS: int
LINE_FRESH_MS: int
TASK_USES_FRESH_MS: int
IDLE_FIELDS: tuple[int, ...]
NICE_FIELD: int
TICK_FIELDS: int

@dataclass(frozen=True, kw_only=True)
class LoadPaths:
    stat: str = '/proc/stat'
    meminfo: str = '/proc/meminfo'
    cpu_pressure: str = '/proc/pressure/cpu'
    memory_pressure: str = '/proc/pressure/memory'
    task_cpu_pressure: str | None = None

@dataclass(frozen=True, kw_only=True)
class CpuTicks:
    busy: int
    total: int
    cpus: int
    nice: int = 0

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class Pressure:
    some_avg10: float
    full_avg10: float | None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class TaskMemory:
    id: str
    mib: int

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class MachineLoad:
    at_ms: int
    cpus: int
    cpu_busy: float | None
    memory_total_mib: int
    memory_available_mib: int
    cpu_some_avg10: float | None
    cpu_full_avg10: float | None
    memory_some_avg10: float | None
    memory_full_avg10: float | None
    cpu_nice: float | None = None
    task_cpu_some_avg10: float | None = None
    task_uses: tuple[TaskMemory, ...] | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class LoadReading:
    load: MachineLoad
    ticks: CpuTicks

def load_read_due(read_ms: int | None, now_ms: int) -> _Program[bool, object]:
    ...

def fresh_line(line: EvictionLine | None, line_at_ms: int | None, now_ms: int) -> _Program[EvictionLine | None, object]:
    ...

def task_uses_of(uses: tuple, uses_at_ms: int | None, now_ms: int) -> _Program[tuple | None, object]:
    ...

def cpu_ticks_of(text: str) -> _Program[CpuTicks, object]:
    ...

def pressure_of(text: str, source: str) -> _Program[Pressure, object]:
    ...

def cpu_busy_between(before: CpuTicks | None, after: CpuTicks) -> _Program[float | None, object]:
    ...

def cpu_nice_between(before: CpuTicks | None, after: CpuTicks) -> _Program[float | None, object]:
    ...

def machine_load_of(stat: str, meminfo: str, cpu_pressure: str | None, memory_pressure: str | None, before: CpuTicks | None, at_ms: int, line: EvictionLine | None=None, task_cpu_pressure: str | None=None) -> _Program[LoadReading, object]:
    ...
