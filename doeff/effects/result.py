"""Result/error handling effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class ResultFailEffect(EffectBase):
    exception: Exception


@dataclass(frozen=True)
class ResultCatchEffect(EffectBase):
    sub_program: ProgramLike
    handler: Callable[[Exception], Any | ProgramLike]


@dataclass(frozen=True)
class ResultRecoverEffect(EffectBase):
    sub_program: ProgramLike
    fallback: Any | ProgramLike | Callable[[Exception], Any | ProgramLike]


@dataclass(frozen=True)
class ResultRetryEffect(EffectBase):
    sub_program: ProgramLike
    max_attempts: int = 3
    delay_ms: int = 0


@dataclass(frozen=True)
class ResultSafeEffect(EffectBase):
    sub_program: ProgramLike


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


__all__ = [
    "ResultFailEffect",
    "ResultCatchEffect",
    "ResultRecoverEffect",
    "ResultRetryEffect",
    "ResultSafeEffect",
    "fail",
    "catch",
    "recover",
    "retry",
    "safe",
    "Fail",
    "Catch",
    "Recover",
    "Retry",
    "Safe",
]
