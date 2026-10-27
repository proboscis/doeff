"""What the doeff-agents CLI asks the session host itself (internal to doeff-agents).

The public effects (``doeff_agents.effects``) carry what a program asks of an
agent session. The CLI's ``agentd`` verbs are an operator's binding to the host
protocol instead — read the host's status or its binding kinds, adopt a seat,
stamp a turn — and ``ps`` must say which of the host's rows it could not read.
These effects carry those asks; ``doeff_agents.handlers._session_host`` answers
them. They are not part of the public API: a program outside doeff-agents asks
for sessions through the public effects.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from doeff_agents.effects.agent import AgentEffectBase, AgentSessionQuery


@dataclass(frozen=True)
class HostStatusReading:
    """The answer to ``SessionHostStatusEffect``: where the host listens and what it says of itself."""

    socket_path: Path
    #: The host's ``daemon.status`` object as it sent it (the JSON boundary — the CLI prints it whole).
    status: Mapping[str, object]
    #: Whether a supervisor (launchd, systemd) is declared to own the host on this socket.
    supervised: bool


@dataclass(frozen=True)
class HostKindsReading:
    """The answer to ``SessionHostKindsEffect``: the binding kinds the host on ``socket_path`` advertises."""

    socket_path: Path
    #: The host's ``kinds.list`` rows as it sent them (the JSON boundary — the CLI prints them whole).
    kinds: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, kw_only=True)
class SessionHostStatusEffect(AgentEffectBase):
    """Ask the host for its status, to tell an operator that a host is up and where.

    Yields: HostStatusReading
    """


@dataclass(frozen=True, kw_only=True)
class SessionHostKindsEffect(AgentEffectBase):
    """Ask the host which binding kinds it serves, so an operator can check a registration.

    Yields: HostKindsReading
    """


@dataclass(frozen=True, kw_only=True)
class SessionHostCallEffect(AgentEffectBase):
    """Send one host-protocol request for a verb that only passes it through (adopt, turn stamps).

    Yields: the host's JSON result for ``method``.
    """

    method: str
    #: The request's params as the host protocol names them (the JSON boundary).
    params: Mapping[str, object]


@dataclass(frozen=True, kw_only=True)
class ListSessionRowsEffect(AgentEffectBase):
    """List the host's sessions together with the rows that could not be read, so ``ps`` can name them.

    Yields: AgentdSessionList (doeff_agents._agentd_client)
    """

    query: AgentSessionQuery = field(default_factory=AgentSessionQuery)

