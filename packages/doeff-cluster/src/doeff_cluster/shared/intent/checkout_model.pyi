# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = checkout_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

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

class ReadCheckout(_doeff_effect_base):
    __doeff_answer__: _doeff_ClassVar[object] = ...
    path: str
    remote: str

class CheckoutRoot(_doeff_effect_base):
    __doeff_answer__: _doeff_ClassVar[object] = ...
    path: str

class SenderSourceRoot(_doeff_effect_base):
    __doeff_answer__: _doeff_ClassVar[object] = ...
