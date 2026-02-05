"""Reader effects and handler for CESK v3.

Reader effects provide read-only environment/config access:
- Ask(key): Retrieve the value for a key from the environment (returns None if missing)
- Local(env_update, sub_program): Run sub_program with a modified environment

Unlike State, Reader is read-only - there's no Put equivalent.
The environment is set once when creating the handler. Local provides
scoped environment modifications for sub-computations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import Forward, Resume, WithHandler
from doeff.do import do
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.program import ProgramBase


@dataclass(frozen=True)
class AskEffect(EffectBase):
    key: str


@dataclass(frozen=True)
class LocalEffect(EffectBase):
    env_update: Mapping[str, Any]
    sub_program: "ProgramBase[Any]"


def Ask(key: str) -> AskEffect:
    return AskEffect(key=key)


def Local(env_update: Mapping[str, Any], sub_program: "ProgramBase[Any]") -> LocalEffect:
    return LocalEffect(env_update=env_update, sub_program=sub_program)


def reader_handler(
    env: dict[str, Any] | None = None,
) -> Callable[[EffectBase], Program[Any]]:
    """Create a reader handler with a fixed environment.

    Handles:
    - Ask(key): Returns env.get(key) or None if missing
    - Local(updates, program): Runs program with merged environment (env | updates)
    """
    environment: dict[str, Any] = dict(env) if env else {}

    @do
    def handler(effect: EffectBase) -> Program[Any]:
        if isinstance(effect, AskEffect):
            return (yield Resume(environment.get(effect.key)))
        if isinstance(effect, LocalEffect):
            merged_env = {**environment, **effect.env_update}
            nested_handler = reader_handler(merged_env)
            result = yield WithHandler(nested_handler, effect.sub_program)
            return (yield Resume(result))
        forwarded = yield Forward(effect)
        return (yield Resume(forwarded))

    return handler


__all__ = [
    "Ask",
    "AskEffect",
    "Local",
    "LocalEffect",
    "reader_handler",
]
