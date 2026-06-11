"""Python @do functions — no __doeff_body__."""
from dataclasses import dataclass

from doeff import EffectBase, do


@dataclass(frozen=True)
class ComputeRisk(EffectBase):
    portfolio: str


@do
def python_risk_check(portfolio):
    """Python @do function. Has effects but no S-expr body."""
    risk = yield ComputeRisk(portfolio=portfolio)
    return risk
