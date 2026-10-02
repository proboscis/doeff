# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = channel_effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from collections import deque as deque
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff import EffectBase as EffectBase
from doeff_core_effects.scheduler import Promise as Promise

@dataclass(eq=False)
class Channel:
    items: deque[object] = ...
    waiters: list[Promise[None]] = ...

@dataclass(frozen=True)
class CreateChannel(EffectBase):
    ...

@dataclass(frozen=True)
class PutChannel(EffectBase):
    channel: Channel
    item: object

@dataclass(frozen=True)
class TakeChannel(EffectBase):
    channel: Channel
