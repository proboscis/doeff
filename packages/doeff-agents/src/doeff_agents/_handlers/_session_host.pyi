# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _session_host.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from doeff.program import ProgramHandler as ProgramHandler
from doeff_time import sync_time_handler as sync_time_handler
from doeff_agents._agentd_client import LazyAgentdClient as LazyAgentdClient
from doeff_agents._agentd_client import agentd_socket_is_supervised as agentd_socket_is_supervised
from doeff_agents._effects._session_host_effects import HostKindsReading as HostKindsReading
from doeff_agents._effects._session_host_effects import HostStatusReading as HostStatusReading
from doeff_agents._effects._session_host_effects import ListSessionRowsEffect as ListSessionRowsEffect
from doeff_agents._effects._session_host_effects import SessionHostCallEffect as SessionHostCallEffect
from doeff_agents._effects._session_host_effects import SessionHostKindsEffect as SessionHostKindsEffect
from doeff_agents._effects._session_host_effects import SessionHostStatusEffect as SessionHostStatusEffect
from doeff_agents._handlers import daemon_agent_handler as daemon_agent_handler
from doeff_agents._handlers.effectful import tmux_agent_defhandler as tmux_agent_defhandler
from doeff_agents._tmux import get_default_backend as get_default_backend

def session_host_defhandler(client: LazyAgentdClient) -> _Handler:
    ...

def session_host_handlers(*, start_host: bool, client: LazyAgentdClient | None=None, time_handler: ProgramHandler | None=None) -> tuple[ProgramHandler, ...]:
    ...

def terminal_session_handlers(*, time_handler: ProgramHandler | None=None) -> tuple[ProgramHandler, ...]:
    ...
