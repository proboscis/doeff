# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = stack_dump_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass

@_doeff_dataclass(frozen=True)
class ArmStackDump(_doeff_effect_base[None]):
    at: float
    seconds: float

@_doeff_dataclass(frozen=True)
class DisarmStackDump(_doeff_effect_base[None]):
    at: float

@_doeff_dataclass(frozen=True)
class ReadStackDumps(_doeff_effect_base[tuple[float, ...]]):
    at: float

@dataclass(frozen=True, kw_only=True)
class StackDumpLedger:
    deadline: float | None
    written: tuple[float, ...]

def stack_dumps_at(ledger: StackDumpLedger, at: float) -> _Program[StackDumpLedger, object]:
    ...
