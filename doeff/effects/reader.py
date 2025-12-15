"""Reader monad effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Mapping
from typing import Any, Callable

from doeff.types import EnvKey

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value
from ._validators import ensure_env_mapping, ensure_hashable, ensure_program_like


@dataclass(frozen=True)
class AskEffect(EffectBase):
    """Looks up the environment entry for key and yields the resolved value."""

    key: EnvKey

    def __post_init__(self) -> None:
        ensure_hashable(self.key, name="key")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "AskEffect":
        return self


@dataclass(frozen=True)
class LocalEffect(EffectBase):
    """Runs a sub-program against an updated environment and yields its value."""

    env_update: Mapping[Any, object]
    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_env_mapping(self.env_update, name="env_update")
        ensure_program_like(self.sub_program, name="sub_program")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "LocalEffect":
        sub_program = intercept_value(self.sub_program, transform)
        if sub_program is self.sub_program:
            return self
        return replace(self, sub_program=sub_program)


def ask(key: EnvKey) -> AskEffect:
    return create_effect_with_trace(AskEffect(key=key))


def local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> LocalEffect:
    return create_effect_with_trace(
        LocalEffect(env_update=env_update, sub_program=sub_program)
    )


def Ask(key: EnvKey) -> Effect:
    return create_effect_with_trace(AskEffect(key=key), skip_frames=3)


def Local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> Effect:
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
