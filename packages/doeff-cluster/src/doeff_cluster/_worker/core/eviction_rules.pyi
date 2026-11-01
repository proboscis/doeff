# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = eviction_rules.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from decimal import Decimal as Decimal
from decimal import InvalidOperation as InvalidOperation
from math import ceil as ceil
import re as re
from doeff_cluster._worker.core.memory_rules import MIB as MIB
from doeff_cluster._worker.core.memory_rules import NodeMemory as NodeMemory
EVICTION_MARGIN_BYTES: int
PODS_CGROUP_NAMES: tuple[str, ...]
KERNEL_SIZE: re.Pattern[str]
KERNEL_UNITS: dict[str, int]
QUANTITY: re.Pattern[str]
QUANTITY_UNITS: dict[None | str, Decimal]

class LineKind(StrEnum):
    ALLOCATABLE = 'allocatable'
    NODE = 'node'

@dataclass(frozen=True, kw_only=True)
class KubeletMemory:
    capacity_bytes: int
    allocatable_bytes: int

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class PodsMemory:
    max_bytes: int | None
    current_bytes: int
    inactive_file_bytes: int

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class EvictionLine:
    kind: LineKind
    size_bytes: int
    used_bytes: int
    room_bytes: int

    def __post_init__(self) -> None:
        ...

def kernel_bytes_of(text: str) -> _Program[int | None, object]:
    ...

def quantity_bytes(text: str) -> _Program[int, object]:
    ...

def stat_bytes(text: str, key: str) -> _Program[int, object]:
    ...

def working_set(used: int, inactive_file: int) -> _Program[int, object]:
    ...

def pods_memory_of(max_text: str, current_text: str, stat_text: str) -> _Program[PodsMemory, object]:
    ...

def root_working_set_of(stat_text: str) -> _Program[int, object]:
    ...

def eviction_line(kubelet: KubeletMemory, pods: PodsMemory, root_working_set: int | None) -> _Program[EvictionLine | None, object]:
    ...

def line_memory(node: NodeMemory, line: EvictionLine | None) -> _Program[NodeMemory, object]:
    ...

def line_name(line: EvictionLine | None) -> _Program[str, object]:
    ...

def jobs_limit(jobs_current: int, line: EvictionLine, margin: int, cap: int | None) -> _Program[int, object]:
    ...

def layer_memory_max(layout: str, layer: str) -> _Program[int | None, object]:
    ...

def pods_dir_of(cgroup_dir: str, host_root: str) -> _Program[str | None, object]:
    ...
