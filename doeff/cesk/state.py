"""CESK machine control and state types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

from doeff._vendor import FrozenDict
from doeff._types_internal import EffectBase
from doeff.cesk.types import Environment, Store
from doeff.cesk.frames import Kontinuation

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback
    from doeff.program import Program


@dataclass(frozen=True)
class Value:
    """Control state: computation has produced a value."""

    v: Any


@dataclass(frozen=True)
class Error:
    """Control state: computation has raised an exception."""

    ex: BaseException
    captured_traceback: CapturedTraceback | None = None


@dataclass(frozen=True)
class EffectControl:
    """Control state: need to handle an effect."""

    effect: EffectBase


@dataclass(frozen=True)
class ProgramControl:
    """Control state: need to execute a program."""

    program: Program


Control: TypeAlias = Value | Error | EffectControl | ProgramControl


@dataclass
class CESKState:
    """Full CESK machine state."""

    C: Control
    E: Environment
    S: Store
    K: Kontinuation

    @classmethod
    def initial(
        cls,
        program: Program,
        env: Environment | dict[Any, Any] | None = None,
        store: Store | None = None,
    ) -> CESKState:
        """Create initial state for a program."""
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        return cls(
            C=ProgramControl(program),
            E=env_frozen,
            S=store if store is not None else {},
            K=[],
        )


__all__ = [
    "Value",
    "Error",
    "EffectControl",
    "ProgramControl",
    "Control",
    "CESKState",
]
