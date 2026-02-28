"""Reader monad effects (Rust-backed core ask effect)."""


from collections.abc import Mapping
from typing import Any

import doeff_vm

from doeff.types import EnvKey

from ._program_types import ProgramLike
from ._validators import ensure_env_mapping, ensure_hashable, ensure_program_like
from .base import Effect


AskEffect = doeff_vm.PyAsk
HashableAskEffect = AskEffect
LocalEffect = doeff_vm.PyLocal


def ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    return AskEffect(key)


def local(env_update: Mapping[Any, object], sub_program: ProgramLike):
    ensure_env_mapping(env_update, name="env_update")
    ensure_program_like(sub_program, name="sub_program")
    return LocalEffect(dict(env_update), sub_program)


def Ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    return AskEffect(key)


def Local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> Effect:
    ensure_env_mapping(env_update, name="env_update")
    ensure_program_like(sub_program, name="sub_program")
    return LocalEffect(dict(env_update), sub_program)


__all__ = [
    "Ask",
    "AskEffect",
    "HashableAskEffect",
    "Local",
    "LocalEffect",
    "ask",
    "local",
]
