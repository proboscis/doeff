from dataclasses import dataclass

from doeff import DoExpr, EffectBase

@dataclass(frozen=True, kw_only=True)
class Computed:
    value: object

@dataclass(frozen=True, kw_only=True)
class ComputeFailed:
    error: Exception

@dataclass(frozen=True)
class Compute(EffectBase):
    program: DoExpr
