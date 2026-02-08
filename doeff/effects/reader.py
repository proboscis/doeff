"""Reader monad effects (Rust-backed core ask effect)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import doeff_vm

from doeff.types import EnvKey

from ._program_types import ProgramLike
from ._validators import ensure_env_mapping, ensure_hashable, ensure_program_like
from .base import Effect, EffectBase, create_effect_with_trace


AskEffect = doeff_vm.PyAsk


@dataclass(frozen=True)
class LocalEffect(EffectBase):
    """Runs a sub-program against an updated environment and yields its value."""

    env_update: Mapping[Any, object]
    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_env_mapping(self.env_update, name="env_update")
        ensure_program_like(self.sub_program, name="sub_program")


def ask(key: EnvKey) -> AskEffect:
    ensure_hashable(key, name="key")
    return create_effect_with_trace(AskEffect(str(key)))


def local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> LocalEffect:
    return create_effect_with_trace(LocalEffect(env_update=env_update, sub_program=sub_program))


def Ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    return create_effect_with_trace(AskEffect(str(key)), skip_frames=3)


def Local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> Effect:
    return create_effect_with_trace(
        LocalEffect(env_update=env_update, sub_program=sub_program), skip_frames=3
    )


__all__ = [
    "Ask",
    "AskEffect",
    "Local",
    "LocalEffect",
    "ask",
    "local",
]
