from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase


class UnhandledEffectError(Exception):
    def __init__(self, effect: EffectBase) -> None:
        self.effect = effect
        super().__init__(f"No handler for {type(effect).__name__}")
