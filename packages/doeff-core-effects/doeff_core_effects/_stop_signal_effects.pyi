# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _stop_signal_effects.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase

@dataclass(frozen=True)
class StopRequested(EffectBase):
    ...

@dataclass(frozen=True)
class AwaitStop(EffectBase):
    ...

@dataclass(frozen=True)
class StopBell(EffectBase):
    ...

@dataclass(frozen=True)
class RaiseStop(EffectBase):
    reason: str
