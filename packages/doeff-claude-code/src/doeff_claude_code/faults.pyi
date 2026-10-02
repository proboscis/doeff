# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = faults.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from doeff import EffectBase as EffectBase

@dataclass(frozen=True)
class ClaudeDropProcess(EffectBase):
    session_id: str

@dataclass(frozen=True)
class ClaudeForgetSession(EffectBase):
    session_id: str
