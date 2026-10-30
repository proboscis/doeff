# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = room_rules.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_cluster._shared.core.capabilities import HOST_PREFIX as HOST_PREFIX
from doeff_cluster._shared.core.capabilities import capabilities_of as capabilities_of
from doeff_cluster._shared.core.capabilities import pool_refusal as pool_refusal
READ_EVERY_MS: int

class RoomState(StrEnum):
    OK = 'ok'
    UNREADABLE = 'unreadable'
    MALFORMED = 'malformed'
TEMPLATE_PROVIDES: tuple[str, ...]
ROOM_FIELDS: tuple[str, ...]

@dataclass(frozen=True, kw_only=True)
class WorkerRoom:
    task_reserve: int
    background_room: int
    memory_mib: int | None
    pools: tuple[str, ...] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class RoomReading:
    state: RoomState
    room: WorkerRoom | None
    reason: str

def room_of_text(text: str, name: str) -> _Program[RoomReading, object]:
    ...

def row_pools(row: dict, name: str) -> _Program[tuple[str, ...] | str, object]:
    ...

def launch_provides(node: str, extra: tuple[str, ...]) -> _Program[tuple[str, ...], object]:
    ...

def announced_provides(provides: tuple[str, ...], pools: tuple[str, ...]) -> _Program[tuple[str, ...], object]:
    ...

def room_warm_services_of(text: str, name: str) -> _Program[bool | None | str, object]:
    ...

def room_read_due(read_ms: int | None, now_ms: int) -> _Program[bool, object]:
    ...

def room_line(before: WorkerRoom, after: WorkerRoom, reading: RoomReading) -> _Program[str, object]:
    ...
