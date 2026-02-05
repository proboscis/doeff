"""Result/error handling effects for CESK v3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase
from doeff.cesk_v3.level2_algebraic_effects.primitives import (
    Forward,
    GetHandlers,
    Resume,
    WithHandler,
)
from doeff.do import do
from doeff.program import Program

if TYPE_CHECKING:
    from doeff.program import ProgramBase

T = TypeVar("T")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def unwrap(self) -> T:
        return self.value


@dataclass(frozen=True)
class Err:
    error: BaseException

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def unwrap(self) -> Any:
        raise self.error


Result = Ok[T] | Err


@dataclass(frozen=True)
class SafeEffect(EffectBase):
    sub_program: "ProgramBase[Any]"


def Safe(sub_program: "ProgramBase[T]") -> SafeEffect:
    return SafeEffect(sub_program=sub_program)


def result_handler() -> Any:
    @do
    def handler(effect: EffectBase) -> Program[Any]:
        if isinstance(effect, SafeEffect):
            handlers = yield GetHandlers()
            wrapped = effect.sub_program
            for h in reversed(handlers):
                wrapped = WithHandler(h, wrapped)
            try:
                result = yield wrapped
                return (yield Resume(Ok(result)))
            except BaseException as e:
                return (yield Resume(Err(e)))
        forwarded = yield Forward(effect)
        return (yield Resume(forwarded))

    return handler


__all__ = [
    "Err",
    "Ok",
    "Result",
    "Safe",
    "SafeEffect",
    "result_handler",
]
