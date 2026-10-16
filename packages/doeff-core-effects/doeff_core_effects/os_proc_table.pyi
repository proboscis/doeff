# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = os_proc_table.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.proc_table_effects import ProcRow as ProcRow
from doeff_core_effects.proc_table_effects import ReadProcTable as ReadProcTable
from doeff_core_effects.proc_table_effects import SignalPid as SignalPid
from doeff_core_effects.proc_table_effects import proc_row_of_stat as proc_row_of_stat
from doeff_core_effects.warm_effects import signal_number_of as signal_number_of
PROC_ROOT: str

class ProcTableUnavailable(RuntimeError):
    ...

def stat_row(pid: int, page_bytes: int) -> _Program[ProcRow | None, object]:
    ...

def proc_root_present() -> _Program[None, object]:
    ...

def os_read_proc_table() -> _Program[tuple, object]:
    ...

def os_signal_pid(pid: int, start_ticks: int, sent: ProcessSignal) -> _Program[bool, object]:
    ...
os_proc_table_handler: _Handler
