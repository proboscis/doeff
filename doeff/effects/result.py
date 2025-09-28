"""Result/error handling effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections.abc import Callable
from typing import Any

from doeff._vendor import Result

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value


@dataclass(frozen=True)
class ResultFailEffect(EffectBase):
    """Immediately raises the provided exception within the program."""

    exception: Exception

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultFailEffect":
        return self


@dataclass(frozen=True)
class ResultCatchEffect(EffectBase):
    """Runs the sub-program and yields either its value or the handler's recovery."""

    sub_program: ProgramLike
    handler: Callable[[Exception], Any | ProgramLike]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultCatchEffect":
        sub_program = intercept_value(self.sub_program, transform)
        handler = intercept_value(self.handler, transform)
        if sub_program is self.sub_program and handler is self.handler:
            return self
        return replace(self, sub_program=sub_program, handler=handler)


@dataclass(frozen=True)
class ResultRecoverEffect(EffectBase):
    """Executes the sub-program and falls back to the provided value or program."""

    sub_program: ProgramLike
    fallback: Any | ProgramLike | Callable[[Exception], Any | ProgramLike]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultRecoverEffect":
        sub_program = intercept_value(self.sub_program, transform)
        fallback = intercept_value(self.fallback, transform)
        if sub_program is self.sub_program and fallback is self.fallback:
            return self
        return replace(self, sub_program=sub_program, fallback=fallback)


@dataclass(frozen=True)
class ResultRetryEffect(EffectBase):
    """Retries the sub-program until success and yields the first successful value."""

    sub_program: ProgramLike
    max_attempts: int = 3
    delay_ms: int = 0

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

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultUnwrapEffect":
        return self


@dataclass(frozen=True)
class ResultFirstSuccessEffect(EffectBase):
    """Try programs sequentially until one succeeds."""

    programs: tuple[ProgramLike, ...]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "ResultFirstSuccessEffect":
        programs = intercept_value(self.programs, transform)
        if programs is self.programs:
            return self
        return replace(self, programs=programs)


def fail(exc: Exception) -> ResultFailEffect:
    return create_effect_with_trace(ResultFailEffect(exception=exc))


def catch(
    sub_program: ProgramLike,
    handler: Callable[[Exception], Any | ProgramLike],
) -> ResultCatchEffect:
    return create_effect_with_trace(
        ResultCatchEffect(sub_program=sub_program, handler=handler)
    )


def recover(
    sub_program: ProgramLike,
    fallback: Any | ProgramLike | Callable[[Exception], Any | ProgramLike],
) -> ResultRecoverEffect:
    return create_effect_with_trace(
        ResultRecoverEffect(sub_program=sub_program, fallback=fallback)
    )


def retry(
    sub_program: ProgramLike,
    max_attempts: int = 3,
    delay_ms: int = 0,
) -> ResultRetryEffect:
    return create_effect_with_trace(
        ResultRetryEffect(
            sub_program=sub_program, max_attempts=max_attempts, delay_ms=delay_ms
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


def Catch(
    sub_program: ProgramLike,
    handler: Callable[[Exception], Any | ProgramLike],
) -> Effect:
    return create_effect_with_trace(
        ResultCatchEffect(sub_program=sub_program, handler=handler), skip_frames=3
    )


def Recover(
    sub_program: ProgramLike,
    fallback: Any | ProgramLike | Callable[[Exception], Any | ProgramLike],
) -> Effect:
    return create_effect_with_trace(
        ResultRecoverEffect(sub_program=sub_program, fallback=fallback), skip_frames=3
    )


def Retry(
    sub_program: ProgramLike,
    max_attempts: int = 3,
    delay_ms: int = 0,
) -> Effect:
    return create_effect_with_trace(
        ResultRetryEffect(
            sub_program=sub_program, max_attempts=max_attempts, delay_ms=delay_ms
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
    "ResultCatchEffect",
    "ResultRecoverEffect",
    "ResultRetryEffect",
    "ResultSafeEffect",
    "ResultUnwrapEffect",
    "ResultFirstSuccessEffect",
    "fail",
    "catch",
    "recover",
    "retry",
    "safe",
    "unwrap_result",
    "first_success_effect",
    "Fail",
    "Catch",
    "Recover",
    "Retry",
    "Safe",
    "Unwrap",
    "FirstSuccess",
]
