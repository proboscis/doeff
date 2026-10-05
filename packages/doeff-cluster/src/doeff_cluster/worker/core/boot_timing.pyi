# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = boot_timing.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.worker.intent.worker_model import BootMarks as BootMarks
from doeff_cluster.worker.intent.worker_model import ProcessStartedMs as ProcessStartedMs
BOOT_STARTED_VAR: str
BOOT_EXEC_VAR: str

@dataclass(frozen=True, kw_only=True)
class BootStamp:
    name: str
    ms: int | None

def process_start_ms(stat_text: str, uptime_text: str, now_ms: int, ticks: int) -> _Program[int | None, object]:
    ...

def env_ms(environ: dict, name: str) -> _Program[int | None, object]:
    ...

def read_boot_marks(environ: dict, imported_ms: int) -> _Program[BootMarks, object]:
    ...

def boot_stamps(marks: BootMarks, answered_ms: int) -> _Program[tuple, object]:
    ...

def stamps_out_of_order(stamps: tuple) -> _Program[tuple, object]:
    ...

def boot_line(marks: BootMarks, answered_ms: int) -> _Program[str, object]:
    ...
