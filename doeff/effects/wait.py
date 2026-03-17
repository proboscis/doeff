from typing import Any, TypeVar

import doeff_vm

from .base import EffectBase
from .spawn import Waitable, normalize_waitable

T = TypeVar("T")

WaitEffect = doeff_vm.WaitEffect


def wait(future: Waitable[T]) -> WaitEffect:
    normalized = normalize_waitable(future)
    if not isinstance(normalized, Waitable):
        raise TypeError(f"Wait requires Waitable, got {type(normalized).__name__}")
    return WaitEffect(future=normalized)


def Wait(future: Waitable[T]):
    normalized = normalize_waitable(future)

    from doeff import do

    @do
    def _program():
        return (yield wait(normalized))

    return _program()


__all__ = [
    "Wait",
    "WaitEffect",
    "wait",
]
