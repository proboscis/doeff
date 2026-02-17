"""Reader monad effects (Rust-backed core ask effect)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import doeff_vm

from doeff.types import EnvKey

from ._program_types import ProgramLike
from ._validators import ensure_env_mapping, ensure_hashable, ensure_program_like
from .base import Effect, create_effect_with_trace


AskEffect = doeff_vm.PyAsk
LocalEffect = doeff_vm.PyLocal


def ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    return create_effect_with_trace(AskEffect(key))


def local(env_update: Mapping[Any, object], sub_program: ProgramLike):
    ensure_env_mapping(env_update, name="env_update")
    ensure_program_like(sub_program, name="sub_program")
    return create_effect_with_trace(LocalEffect(dict(env_update), sub_program))


def Ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    return create_effect_with_trace(AskEffect(key), skip_frames=3)


def Local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> Effect:
    ensure_env_mapping(env_update, name="env_update")
    ensure_program_like(sub_program, name="sub_program")
    return create_effect_with_trace(LocalEffect(dict(env_update), sub_program), skip_frames=3)


__all__ = [
    "Ask",
    "AskEffect",
    "Local",
    "LocalEffect",
    "ask",
    "local",
]
