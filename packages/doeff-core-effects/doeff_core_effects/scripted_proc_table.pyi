# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = scripted_proc_table.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff_hy._static_types import Handler as _Handler
from doeff_core_effects.proc_table_effects import ReadProcTable as ReadProcTable
from doeff_core_effects.proc_table_effects import SignalPid as SignalPid

def scripted_proc_table_handler(rows: tuple) -> _Handler:
    ...
