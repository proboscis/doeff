from collections import deque
from dataclasses import dataclass, field

from doeff import EffectBase
from doeff_core_effects.scheduler import Promise

@dataclass(eq=False)
class Channel:
    items: deque[object] = field(default_factory=deque)
    waiters: list[Promise[None]] = field(default_factory=list)

@dataclass(frozen=True)
class CreateChannel(EffectBase): ...

@dataclass(frozen=True)
class PutChannel(EffectBase):
    channel: Channel
    item: object

@dataclass(frozen=True)
class TakeChannel(EffectBase):
    channel: Channel
