"""同一VM内のworker実行。Hyのcomposition rootが公開する型。"""

from collections.abc import Callable

from doeff import EffectBase, K, Pass, Program, Resume
from doeff_agents.sessionhost.acp.effects import AgentdSettings, AgentdState
from doeff_agents.sessionhost.acp.loop_model import LoopPorts

Dispatcher = Callable[[EffectBase, K], Resume | Pass]

def concurrent_worker(
    settings: AgentdSettings,
    state: AgentdState,
    normal_dispatchers: tuple[Dispatcher, ...],
    cache_dispatchers: tuple[Dispatcher, ...],
    ports: LoopPorts,
) -> Program: ...
