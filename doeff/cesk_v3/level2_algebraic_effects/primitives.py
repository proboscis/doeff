from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from doeff.cesk_v3.level2_algebraic_effects.frames import EffectBase, Handler

if TYPE_CHECKING:
    from doeff.program import Program

T = TypeVar("T")


class ControlPrimitive:
    pass


@dataclass(frozen=True)
class WithHandler(ControlPrimitive, Generic[T]):
    handler: Handler
    program: Program[T]


@dataclass(frozen=True)
class Resume(ControlPrimitive):
    value: Any


@dataclass(frozen=True)
class Forward(ControlPrimitive):
    effect: EffectBase
