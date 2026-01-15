"""Result/error handling effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import Any

from doeff._vendor import Result

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value
from ._validators import (
    ensure_callable,
    ensure_exception,
    ensure_non_negative_int,
    ensure_positive_int,
    ensure_program_like,
    ensure_program_like_or_thunk,
    ensure_program_tuple,
)


@dataclass(frozen=True)
class ResultFailEffect(EffectBase):
    """Immediately raises the provided exception within the program."""

    exception: Exception

    def __post_init__(self) -> None:
        ensure_exception(self.exception, name="exception")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultFailEffect":
        return self


@dataclass(frozen=True)
class ResultFinallyEffect(EffectBase):
    """Runs a sub-program and always executes the finalizer afterwards."""

    sub_program: ProgramLike
    finalizer: ProgramLike | Callable[[], Any | ProgramLike]

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")
        ensure_program_like_or_thunk(self.finalizer, name="finalizer")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultFinallyEffect":
        sub_program = intercept_value(self.sub_program, transform)
        finalizer = intercept_value(self.finalizer, transform)
        if sub_program is self.sub_program and finalizer is self.finalizer:
            return self
        return replace(self, sub_program=sub_program, finalizer=finalizer)


@dataclass(frozen=True)
class ResultRetryEffect(EffectBase):
    """Retries the sub-program until success and yields the first successful value."""

    sub_program: ProgramLike
    max_attempts: int = 3
    delay_ms: int = 0
    delay_strategy: Callable[[int, Exception | None], float | int | None] | None = None

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")
        ensure_positive_int(self.max_attempts, name="max_attempts")
        ensure_non_negative_int(self.delay_ms, name="delay_ms")
        if self.delay_strategy is not None:
            ensure_callable(self.delay_strategy, name="delay_strategy")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultRetryEffect":
        sub_program = intercept_value(self.sub_program, transform)
        if sub_program is self.sub_program:
            return self
        return replace(self, sub_program=sub_program)


@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    """Runs the sub-program and yields a Result for success/failure."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultSafeEffect":
        sub_program = intercept_value(self.sub_program, transform)
        if sub_program is self.sub_program:
            return self
        return replace(self, sub_program=sub_program)


@dataclass(frozen=True)
class ResultUnwrapEffect(EffectBase):
    """Unwrap a Result, yielding the value or raising the stored error."""

    result: Result[Any]

    def __post_init__(self) -> None:
        if not isinstance(self.result, Result):
            raise TypeError(
                "result must be Result, got "
                f"{type(self.result).__name__}"
            )

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultUnwrapEffect":
        return self


@dataclass(frozen=True)
class ResultFirstSuccessEffect(EffectBase):
    """Try programs sequentially until one succeeds."""

    programs: tuple[ProgramLike, ...]

    def __post_init__(self) -> None:
        ensure_program_tuple(self.programs, name="programs")
        if not self.programs:
            raise ValueError("programs must not be empty")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultFirstSuccessEffect":
        programs = intercept_value(self.programs, transform)
        if programs is self.programs:
            return self
        return replace(self, programs=programs)


def fail(exc: Exception) -> ResultFailEffect:
    return create_effect_with_trace(ResultFailEffect(exception=exc))


def finally_(
    sub_program: ProgramLike,
    finalizer: ProgramLike | Callable[[], Any | ProgramLike],
) -> ResultFinallyEffect:
    return create_effect_with_trace(
        ResultFinallyEffect(sub_program=sub_program, finalizer=finalizer)
    )


def retry(
    sub_program: ProgramLike,
    max_attempts: int = 3,
    delay_ms: int = 0,
    delay_strategy: Callable[[int, Exception | None], float | int | None] | None = None,
) -> ResultRetryEffect:
    return create_effect_with_trace(
        ResultRetryEffect(
            sub_program=sub_program,
            max_attempts=max_attempts,
            delay_ms=delay_ms,
            delay_strategy=delay_strategy,
        )
    )


def safe(sub_program: ProgramLike) -> ResultSafeEffect:
    return create_effect_with_trace(ResultSafeEffect(sub_program=sub_program))


def unwrap_result(result: Result[Any]) -> ResultUnwrapEffect:
    return create_effect_with_trace(ResultUnwrapEffect(result=result))


def first_success_effect(*programs: ProgramLike) -> ResultFirstSuccessEffect:
    if not programs:
        raise ValueError("first_success_effect requires at least one program")
    return create_effect_with_trace(
        ResultFirstSuccessEffect(programs=tuple(programs))
    )


def Fail(exc: Exception) -> Effect:
    return create_effect_with_trace(ResultFailEffect(exception=exc), skip_frames=3)


def Finally(
    sub_program: ProgramLike,
    finalizer: ProgramLike | Callable[[], Any | ProgramLike],
) -> Effect:
    return create_effect_with_trace(
        ResultFinallyEffect(sub_program=sub_program, finalizer=finalizer), skip_frames=3
    )


def Retry(
    sub_program: ProgramLike,
    max_attempts: int = 3,
    delay_ms: int = 0,
    delay_strategy: Callable[[int, Exception | None], float | int | None] | None = None,
) -> Effect:
    return create_effect_with_trace(
        ResultRetryEffect(
            sub_program=sub_program,
            max_attempts=max_attempts,
            delay_ms=delay_ms,
            delay_strategy=delay_strategy,
        ),
        skip_frames=3,
    )


def Safe(sub_program: ProgramLike) -> Effect:
    return create_effect_with_trace(
        ResultSafeEffect(sub_program=sub_program),
        skip_frames=3,
    )


def Unwrap(result: Result[Any]) -> Effect:
    return create_effect_with_trace(
        ResultUnwrapEffect(result=result),
        skip_frames=3,
    )


def FirstSuccess(*programs: ProgramLike) -> Effect:
    return create_effect_with_trace(
        ResultFirstSuccessEffect(programs=tuple(programs)),
        skip_frames=3,
    )


__all__ = [
    "ResultFailEffect",
    "ResultFinallyEffect",
    "ResultRetryEffect",
    "ResultSafeEffect",
    "ResultUnwrapEffect",
    "ResultFirstSuccessEffect",
    "fail",
    "finally_",
    "retry",
    "safe",
    "unwrap_result",
    "first_success_effect",
    "Fail",
    "Finally",
    "Retry",
    "Safe",
    "Unwrap",
    "FirstSuccess",
]
