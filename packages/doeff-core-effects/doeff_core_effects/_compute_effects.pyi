# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _compute_effects.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from dataclasses import dataclass as dataclass
from doeff import DoExpr as DoExpr
from doeff import EffectBase as EffectBase

@dataclass(frozen=True, kw_only=True)
class Computed:
    value: object

@dataclass(frozen=True, kw_only=True)
class ComputeFailed:
    error: Exception

@dataclass(frozen=True)
class Compute(EffectBase[Computed | ComputeFailed]):
    program: DoExpr

@dataclass(frozen=True, kw_only=True)
class ComputeLoad:
    queued: int
    running: int
    started: int
    waited_seconds: float
    ran_seconds: float

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ReadComputeLoad(EffectBase[ComputeLoad]):
    ...
