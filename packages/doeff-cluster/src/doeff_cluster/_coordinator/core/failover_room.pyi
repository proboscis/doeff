# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = failover_room.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster._coordinator.intent.cluster_model import WorkerInfo as WorkerInfo
from doeff_cluster._coordinator.intent.failover_model import ExtraProvides as ExtraProvides
from doeff_cluster._coordinator.intent.failover_model import FailoverWorker as FailoverWorker
from doeff_cluster._worker.core.room_rules import RoomReading as RoomReading
from doeff_cluster._worker.core.room_rules import RoomState as RoomState
from doeff_cluster._worker.core.room_rules import announced_provides as announced_provides
from doeff_cluster._worker.core.room_rules import launch_provides as launch_provides
from doeff_cluster._worker.core.room_rules import room_of_text as room_of_text

def room_row_names(text: str) -> _Program[tuple[str, ...], object]:
    ...

def room_file_workers(text: str, extras: tuple[ExtraProvides, ...]) -> _Program[tuple[FailoverWorker, ...], object]:
    ...
