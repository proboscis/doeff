# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = proc_table_effects.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff_core_effects.process_effects import ProcessSignal as ProcessSignal
from doeff_core_effects.process_stat import ENDED_STATES as ENDED_STATES
STAT_PPID: int
STAT_START_TICKS: int
STAT_RSS_PAGES: int

@dataclass(frozen=True, kw_only=True)
class ProcRow:
    pid: int
    ppid: int
    start_ticks: int
    rss_bytes: int
    comm: str

    def __post_init__(self) -> None:
        ...

def proc_row_of_stat(pid: int, text: str, page_bytes: int) -> _Program[ProcRow | None, object]:
    ...

@_doeff_dataclass(frozen=True)
class ReadProcTable(_doeff_effect_base[tuple]):
    ...

@_doeff_dataclass(frozen=True)
class SignalPid(_doeff_effect_base[bool]):
    pid: int
    start_ticks: int
    signal: ProcessSignal
