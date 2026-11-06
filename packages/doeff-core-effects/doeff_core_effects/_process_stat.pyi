# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _process_stat.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass
ENDED_STATES: frozenset[str]

@dataclass(frozen=True, kw_only=True)
class ProcStat:
    state: str
    start_ticks: int
