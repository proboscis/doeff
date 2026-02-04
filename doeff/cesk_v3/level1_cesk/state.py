from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from doeff.cesk_v3.level1_cesk.types import Environment, Store

if TYPE_CHECKING:
    from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
    from doeff.cesk_v3.level2_algebraic_effects.frames import (
        DispatchingFrame,
        WithHandlerFrame,
    )

    Frame = ReturnFrame | WithHandlerFrame | DispatchingFrame
    Kontinuation = list[Frame]


@dataclass(frozen=True)
class ProgramControl:
    program: Any


@dataclass(frozen=True)
class Value:
    value: Any


@dataclass(frozen=True)
class Error:
    error: BaseException


@dataclass(frozen=True)
class EffectYield:
    yielded: Any


@dataclass(frozen=True)
class Done:
    value: Any


@dataclass(frozen=True)
class Failed:
    error: BaseException


Control = ProgramControl | Value | Error | EffectYield


@dataclass(frozen=True)
class CESKState:
    C: Control
    E: Environment
    S: Store
    K: Kontinuation
