"""stop_signal_effects.hy の公開面の型(止めの合図の effect — 型検査を受ける消費者向け)。"""

from dataclasses import dataclass

from doeff import EffectBase

@dataclass(frozen=True)
class StopRequested(EffectBase): ...

@dataclass(frozen=True)
class RaiseStop(EffectBase):
    reason: str
