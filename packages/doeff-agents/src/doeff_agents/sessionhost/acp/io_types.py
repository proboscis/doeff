"""I/O境界が返す同種の集合と、意味の異なる値の組。"""
from typing import TypeAlias

from doeff_agents.sessionhost.acp.effects import AcpRow, PaneSeat, ProfileHome, ProfileUsageOutcome

AcpRows: TypeAlias = tuple[AcpRow, ...]
ProfileHomes: TypeAlias = tuple[ProfileHome, ...]
ProfileUsageOutcomes: TypeAlias = tuple[ProfileUsageOutcome, ...]
PaneSeats: TypeAlias = tuple[PaneSeat, ...]
Paths: TypeAlias = tuple[str, ...]
Offsets: TypeAlias = tuple[int, ...]
SeatEnvPairs: TypeAlias = tuple[tuple[str, str], ...]
NodePlaces: TypeAlias = tuple[str, ...]
