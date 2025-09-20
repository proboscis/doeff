"""Reader monad effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class AskEffect(EffectBase):
    """Looks up the environment entry for key and yields the resolved value."""

    key: str


@dataclass(frozen=True)
class LocalEffect(EffectBase):
    """Runs a sub-program against an updated environment and yields its value."""

    env_update: Mapping[str, object]
    sub_program: ProgramLike


def ask(key: str) -> AskEffect:
    return create_effect_with_trace(AskEffect(key=key))


def local(env_update: Mapping[str, object], sub_program: ProgramLike) -> LocalEffect:
    return create_effect_with_trace(
        LocalEffect(env_update=env_update, sub_program=sub_program)
    )


def Ask(key: str) -> Effect:
    return create_effect_with_trace(AskEffect(key=key), skip_frames=3)


def Local(env_update: Mapping[str, object], sub_program: ProgramLike) -> Effect:
    return create_effect_with_trace(
        LocalEffect(env_update=env_update, sub_program=sub_program), skip_frames=3
    )


__all__ = [
    "AskEffect",
    "LocalEffect",
    "ask",
    "local",
    "Ask",
    "Local",
]
