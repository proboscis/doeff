# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = checkout_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class LocalCheckout:
    name: str
    path: str
    remote: str = 'origin'

@dataclass(frozen=True, kw_only=True)
class ProjectOfCheckout:
    repo: str
    path: str
    python: str
    groups: tuple = ...
    native: tuple = ...

@dataclass(frozen=True, kw_only=True)
class CheckoutState:
    head: str
    url: str
    dirty: bool
    on_remote: bool

@_doeff_dataclass(frozen=True)
class ReadCheckout(_doeff_effect_base[CheckoutState]):
    path: str
    remote: str

@_doeff_dataclass(frozen=True)
class CheckoutRoot(_doeff_effect_base[str | None]):
    path: str

@_doeff_dataclass(frozen=True)
class SenderSourceRoot(_doeff_effect_base[str | None]):
    ...
