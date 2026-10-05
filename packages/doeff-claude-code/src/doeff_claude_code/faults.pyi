# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = faults.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff import EffectBase as EffectBase

@dataclass(frozen=True)
class ClaudeDropProcess(EffectBase):
    session_id: str

@dataclass(frozen=True)
class ClaudeForgetSession(EffectBase):
    session_id: str

@dataclass(frozen=True)
class ClaudeEmitOutsideTurn(EffectBase):
    session_id: str

@dataclass(frozen=True)
class ClaudeLiveProcess(EffectBase):
    session_id: str

class StopReason(StrEnum):
    TURN_END = 'turn-end'
    OUTSIDE_TURN_OUTPUT = 'outside-turn-output'

@dataclass(frozen=True, kw_only=True)
class LiveProcess:
    launches: int

@dataclass(frozen=True, kw_only=True)
class NoLiveProcess:
    launches: int
    stopped_because: StopReason | None
