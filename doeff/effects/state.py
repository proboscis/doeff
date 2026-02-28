"""State monad effects (Rust-backed core effects)."""


from collections.abc import Callable
from typing import Any

import doeff_vm

from ._validators import ensure_callable, ensure_str
from .base import Effect


StateGetEffect = doeff_vm.PyGet
StatePutEffect = doeff_vm.PyPut
StateModifyEffect = doeff_vm.PyModify


def get(key: str) -> StateGetEffect:
    ensure_str(key, name="key")
    return StateGetEffect(key)


def put(key: str, value: Any) -> StatePutEffect:
    ensure_str(key, name="key")
    return StatePutEffect(key, value)


def modify(key: str, f: Callable[[Any], Any]) -> StateModifyEffect:
    ensure_str(key, name="key")
    ensure_callable(f, name="func")
    return StateModifyEffect(key, f)


def Get(key: str) -> Effect:
    ensure_str(key, name="key")
    return StateGetEffect(key)


def Put(key: str, value: Any) -> Effect:
    ensure_str(key, name="key")
    return StatePutEffect(key, value)


def Modify(key: str, f: Callable[[Any], Any]) -> Effect:
    ensure_str(key, name="key")
    ensure_callable(f, name="func")
    return StateModifyEffect(key, f)


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
