"""Thread execution effect."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Literal

from ._program_types import ProgramLike
from ._validators import ensure_program_like, ensure_str
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


ThreadStrategy = Literal["dedicated", "pooled", "daemon"]

_DEFAULT_STRATEGY: ThreadStrategy = "pooled"
_VALID_STRATEGIES: tuple[ThreadStrategy, ...] = ("dedicated", "pooled", "daemon")


@dataclass(frozen=True)
class ThreadEffect(EffectBase):
    """Runs the provided program on a separate thread and returns its result."""

    program: ProgramLike
    strategy: ThreadStrategy = _DEFAULT_STRATEGY
    await_result: bool = True

    def __post_init__(self) -> None:
        ensure_program_like(self.program, name="program")
        ensure_str(self.strategy, name="strategy")
        if self.strategy not in _VALID_STRATEGIES:
            raise ValueError(
                "strategy must be one of 'dedicated', 'pooled', or 'daemon', "
                f"got {self.strategy!r}"
            )
        if not isinstance(self.await_result, bool):
            raise TypeError(
                f"await_result must be bool, got {type(self.await_result).__name__}"
            )

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ThreadEffect":
        program = intercept_value(self.program, transform)
        if program is self.program:
            return self
        return replace(self, program=program)


def thread(
    program: ProgramLike,
    *,
    strategy: ThreadStrategy = _DEFAULT_STRATEGY,
    await_result: bool = True,
) -> ThreadEffect:
    return create_effect_with_trace(
        ThreadEffect(program=program, strategy=strategy, await_result=await_result)
    )


def Thread(
    program: ProgramLike,
    *,
    strategy: ThreadStrategy = _DEFAULT_STRATEGY,
    await_result: bool = True,
) -> Effect:
    return create_effect_with_trace(
        ThreadEffect(
            program=program,
            strategy=strategy,
            await_result=await_result,
        ),
        skip_frames=3,
    )


__all__ = [
    "ThreadEffect",
    "ThreadStrategy",
    "thread",
    "Thread",
]
