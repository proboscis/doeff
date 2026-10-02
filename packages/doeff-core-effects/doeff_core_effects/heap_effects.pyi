# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = heap_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from typing import ClassVar as _doeff_ClassVar

@_doeff_dataclass(frozen=True)
class CollectAndFreeze(_doeff_effect_base[int]):
    __doeff_answer__: _doeff_ClassVar[object] = ...
