# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from typing import ClassVar as _doeff_ClassVar
from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class ProcessEnded:
    job: str
    instance: str
    worker: str

@dataclass(frozen=True, kw_only=True)
class ProcessWaitExpired:
    job: str
    waited_seconds: float

@_doeff_dataclass(frozen=True)
class AwaitProcessEnded(_doeff_effect_base[ProcessEnded | ProcessWaitExpired]):
    __doeff_answer__: _doeff_ClassVar[object] = ...
    job: str
    timeout_seconds: float | None = None
