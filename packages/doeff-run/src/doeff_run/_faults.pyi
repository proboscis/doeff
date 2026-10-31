# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _faults.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from doeff_run._values import RunHandle as RunHandle

@_doeff_dataclass(frozen=True)
class RunVanish(_doeff_effect_base[bool]):
    handle: RunHandle

    def __post_init__(self) -> None:
        ...
