"""State monad effects (Rust-backed core effects)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import doeff_vm

from ._validators import ensure_callable, ensure_str
from .base import Effect, create_effect_with_trace


StateGetEffect = doeff_vm.PyGet
StatePutEffect = doeff_vm.PyPut
StateModifyEffect = doeff_vm.PyModify


def get(key: str) -> StateGetEffect:
    ensure_str(key, name="key")
    return create_effect_with_trace(StateGetEffect(key))


def put(key: str, value: Any) -> StatePutEffect:
    ensure_str(key, name="key")
    return create_effect_with_trace(StatePutEffect(key, value))


def modify(key: str, f: Callable[[Any], Any]) -> StateModifyEffect:
    ensure_str(key, name="key")
    ensure_callable(f, name="func")
    return create_effect_with_trace(StateModifyEffect(key, f))


def Get(key: str) -> Effect:
    ensure_str(key, name="key")
    return create_effect_with_trace(StateGetEffect(key), skip_frames=3)


def Put(key: str, value: Any) -> Effect:
    ensure_str(key, name="key")
    return create_effect_with_trace(StatePutEffect(key, value), skip_frames=3)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    ensure_str(key, name="key")
    ensure_callable(f, name="func")
    return create_effect_with_trace(StateModifyEffect(key, f), skip_frames=3)


__all__ = [
    "Get",
    "Modify",
    "Put",
    "StateGetEffect",
    "StateModifyEffect",
    "StatePutEffect",
    "get",
    "modify",
    "put",
]
