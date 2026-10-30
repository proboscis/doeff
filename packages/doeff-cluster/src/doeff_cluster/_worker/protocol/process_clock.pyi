# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = process_clock.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff_hy._static_types import Handler as _Handler
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ReadText as ReadText
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_cluster._worker.intent.worker_model import ProcessStartedMs as ProcessStartedMs
from doeff_cluster._worker.core.boot_timing import process_start_ms as process_start_ms

def process_clock(ticks: int) -> _Handler:
    ...
