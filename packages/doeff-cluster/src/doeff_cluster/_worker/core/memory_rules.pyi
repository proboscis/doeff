# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = memory_rules.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from bisect import bisect_left as bisect_left
from operator import attrgetter as attrgetter
from itertools import accumulate as accumulate
from itertools import chain as chain
from itertools import groupby as groupby
from itertools import islice as islice
from itertools import repeat as repeat
from itertools import takewhile as takewhile
from doeff_traverse import sequence as sequence
from doeff_core_effects import ProcRow as ProcRow
MIB: int

class MemoryLevel(StrEnum):
    CALM = 'calm'
    TIGHT = 'tight'
    DANGER = 'danger'

class UnitKind(StrEnum):
    BRANCH = 'branch'
    LEAF = 'leaf'
    BODY = 'body'
    TASK = 'task'
DOMINANT_SHARE: float
TASK_PREFIX: str

@dataclass(frozen=True, kw_only=True)
class MemoryLimits:
    danger_available: float = 0.1
    danger_pressured_available: float = 0.2
    danger_full_avg10: float = 25.0
    tight_available: float = 0.2
    tight_full_avg10: float = 10.0
    min_victim_bytes: int = ...
    steady_ratio: float = 1.5
    swarm_share: float = 0.25

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class NodeMemory:
    total_bytes: int
    available_bytes: int
    some_avg10: float | None
    full_avg10: float | None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class ContainerMemory:
    current_bytes: int | None
    peak_bytes: int | None

@dataclass(frozen=True, kw_only=True)
class JobRoot:
    name: str
    pid: int

def hyx_task_rootXquestion_markX(root: JobRoot) -> _Program[bool, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class JobUse:
    job: str
    pid: int
    bytes: int
    processes: int

@dataclass(frozen=True, kw_only=True)
class KillUnit:
    job: str
    kind: UnitKind
    head: ProcRow
    bytes: int
    members: tuple[ProcRow, ...]

@dataclass(frozen=True, kw_only=True)
class JobSteady:
    job: str
    bytes: int

def steady_sizes(windows: tuple) -> _Program[tuple, object]:
    ...

def hyx_beyond_steadyXquestion_markX(unit: KillUnit, uses: tuple, steadies: tuple, limits: MemoryLimits) -> _Program[bool, object]:
    ...

def spared_steady(units: tuple, uses: tuple, steadies: tuple, limits: MemoryLimits) -> _Program[tuple, object]:
    ...

def mib_of(size: int) -> _Program[int, object]:
    ...

def meminfo_kib(text: str, key: str) -> _Program[int, object]:
    ...

def pressure_avg10(text: str, kind: str) -> _Program[float, object]:
    ...

def node_memory_of(meminfo: str, pressure: str | None) -> _Program[NodeMemory, object]:
    ...

def cgroup_bytes_of(text: str | None) -> _Program[int | None, object]:
    ...

def memory_level(node: NodeMemory, limits: MemoryLimits) -> _Program[MemoryLevel, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class ProcTree:
    by_pid: tuple[ProcRow, ...]
    pids: tuple[int, ...]
    children: dict[int, tuple]

def proc_tree_of(rows: tuple) -> _Program[ProcTree, object]:
    ...

def row_of(tree: ProcTree, pid: int) -> _Program[ProcRow | None, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class _TreeWalk:
    members: tuple[ProcRow, ...]
    sizes: tuple[int, ...]
    counts: tuple[int, ...]
    kids: tuple[tuple, ...]

def _walk_of(tree: ProcTree, row: ProcRow) -> _Program[_TreeWalk, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class _JobWalk:
    root: JobRoot
    walk: _TreeWalk

def _job_walks(rows: tuple, roots: tuple) -> _Program[tuple, object]:
    ...

def _uses_of(walks: tuple) -> _Program[tuple, object]:
    ...

def _job_uses(rows: tuple, roots: tuple) -> _Program[tuple, object]:
    ...

@dataclass(frozen=True, kw_only=True)
class Gathering:
    point: int
    sides: tuple[int, ...]

def gathering_of(walk: _TreeWalk) -> _Program[Gathering, object]:
    ...

def units_of_job(walk: _TreeWalk, root: JobRoot) -> _Program[tuple, object]:
    ...

def _units_of(walks: tuple) -> _Program[tuple, object]:
    ...

def _kill_units(rows: tuple, roots: tuple) -> _Program[tuple, object]:
    ...

def victim_of(units: tuple, uses: tuple, steadies: tuple, limits: MemoryLimits) -> _Program[KillUnit | None, object]:
    ...

def swarm_need(node: NodeMemory, limits: MemoryLimits) -> _Program[int, object]:
    ...

def swarm_of(units: tuple, uses: tuple, node: NodeMemory, container_bytes: int | None, limits: MemoryLimits) -> _Program[tuple, object]:
    ...
