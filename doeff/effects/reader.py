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
class HashableAskEffect(EffectBase):
    """Reader lookup effect that preserves non-string hashable keys."""

    key: EnvKey


@dataclass(frozen=True)
class LocalEffect(EffectBase):
    """Runs a sub-program against an updated environment and yields its value."""

    env_update: Mapping[Any, object]
    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_env_mapping(self.env_update, name="env_update")
        ensure_program_like(self.sub_program, name="sub_program")


def ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    if isinstance(key, str):
        return create_effect_with_trace(AskEffect(key))
    return create_effect_with_trace(HashableAskEffect(key=key))


def _build_local_overlay(env_update: Mapping[Any, object]) -> dict[Any, object]:
    overlay: dict[Any, object] = {}
    for key, value in env_update.items():
        overlay[key] = value
        if not isinstance(key, str):
            overlay[str(key)] = value
    return overlay


def local(env_update: Mapping[Any, object], sub_program: ProgramLike):
    ensure_env_mapping(env_update, name="env_update")
    ensure_program_like(sub_program, name="sub_program")

    overlay = _build_local_overlay(env_update)

    def handle_local_ask(effect, k):
        if isinstance(effect, AskEffect) and effect.key in overlay:
            return (yield doeff_vm.Resume(k, overlay[effect.key]))
        if isinstance(effect, HashableAskEffect) and effect.key in overlay:
            return (yield doeff_vm.Resume(k, overlay[effect.key]))
        yield doeff_vm.Delegate()

    return doeff_vm.WithHandler(handle_local_ask, sub_program)


def Ask(key: EnvKey) -> Effect:
    ensure_hashable(key, name="key")
    if isinstance(key, str):
        return create_effect_with_trace(AskEffect(key), skip_frames=3)
    return create_effect_with_trace(HashableAskEffect(key=key), skip_frames=3)


def Local(env_update: Mapping[Any, object], sub_program: ProgramLike) -> Effect:
    ensure_env_mapping(env_update, name="env_update")
    ensure_program_like(sub_program, name="sub_program")

    overlay = _build_local_overlay(env_update)

    def handle_local_ask(effect, k):
        if isinstance(effect, AskEffect) and effect.key in overlay:
            return (yield doeff_vm.Resume(k, overlay[effect.key]))
        if isinstance(effect, HashableAskEffect) and effect.key in overlay:
            return (yield doeff_vm.Resume(k, overlay[effect.key]))
        yield doeff_vm.Delegate()

    return doeff_vm.WithHandler(handle_local_ask, sub_program)


__all__ = [
    "Ask",
    "AskEffect",
    "HashableAskEffect",
    "Local",
    "LocalEffect",
    "ask",
    "local",
]
