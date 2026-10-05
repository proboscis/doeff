# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = retirement_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff_cluster.worker.intent.worker_model import Retired as Retired
from doeff_cluster.worker.intent.worker_model import HandoffAbandoned as HandoffAbandoned
Retirement: TypeAlias = Retired | HandoffAbandoned

@_doeff_dataclass(frozen=True)
class AwaitRetirement(_doeff_effect_base[Retired | HandoffAbandoned]):
    after: Retired | HandoffAbandoned | None = None
