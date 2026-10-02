# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = compute_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

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
class Compute(EffectBase):
    program: DoExpr
