"""Reader effects and handler for CESK v3.

Reader effects provide read-only environment/config access:
- Ask(key): Retrieve the value for a key from the environment (returns None if missing)

Unlike State, Reader is read-only - there's no Put equivalent.
The environment is set once when creating the handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume
from doeff.do import do
from doeff.program import Program


@dataclass(frozen=True)
class AskEffect(EffectBase):
    """Retrieves a value from the read-only environment."""

    key: str


def Ask(key: str) -> AskEffect:
    """Create a reader ask effect."""
    return AskEffect(key=key)


def reader_handler(
    env: dict[str, Any] | None = None,
) -> Callable[[EffectBase], Program[Any]]:
    """Create a reader handler with a fixed environment.

    The environment is immutable - all Ask effects receive values from
    the same dictionary snapshot.
    """
    environment: dict[str, Any] = dict(env) if env else {}

    @do
    def handler(effect: EffectBase) -> Program[Any]:
        if isinstance(effect, AskEffect):
            return (yield Resume(environment.get(effect.key)))
        return (yield Forward(effect))

    return handler


__all__ = [
    "Ask",
    "AskEffect",
    "reader_handler",
]
