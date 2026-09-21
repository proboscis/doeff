"""workerの停止・排水・観測をVMへ渡す、通常処理と専用操作で共通の境界。"""

from collections.abc import Callable
from dataclasses import dataclass

from doeff import EffectBase
from doeff_agents.sessionhost.acp.effects import AgentdState


@dataclass(frozen=True)
class LoopControl:
    stopping: bool
    draining: bool


@dataclass(frozen=True)
class LoopPorts:
    stopping: Callable[[], bool]
    draining: Callable[[], bool]
    publish: Callable[[AgentdState], None]
    log: Callable[[str], None]


@dataclass(frozen=True)
class ReadLoopControl(EffectBase):
    pass


@dataclass(frozen=True)
class PublishLoopState(EffectBase):
    state: AgentdState


@dataclass(frozen=True)
class LoopDelay(EffectBase):
    seconds: float


@dataclass(frozen=True)
class LoopLog(EffectBase):
    text: str
