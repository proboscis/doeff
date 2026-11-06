# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _stop_signal_effects.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

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
