
from dataclasses import dataclass
from typing import Any, TypeVar

from .base import Effect, EffectBase
from .gather import gather
from .spawn import Waitable, normalize_waitable

T = TypeVar("T")


@dataclass(frozen=True)
class WaitEffect(EffectBase):
    future: Waitable[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.future, Waitable):
            raise TypeError(f"Wait requires Waitable, got {type(self.future).__name__}")


def wait(future: Waitable[T]) -> WaitEffect:
    normalized = normalize_waitable(future)
    return WaitEffect(future=normalized)


def Wait(future: Waitable[T]):
    normalized = normalize_waitable(future)

    from doeff import do

    @do
    def _program():
        values = yield gather(normalized)
        return values[0]

    return _program()


__all__ = [
    "Wait",
    "WaitEffect",
    "wait",
]
