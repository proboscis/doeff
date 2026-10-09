# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = latest_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass

@_doeff_dataclass(frozen=True)
class PublishLatest(_doeff_effect_base[None]):
    value: object

@_doeff_dataclass(frozen=True)
class ReadLatest(_doeff_effect_base[object | None]):
    kind: type

@_doeff_dataclass(frozen=True)
class AwaitLatest(_doeff_effect_base[object | None]):
    kind: type
    seen: object
