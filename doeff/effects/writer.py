"""Writer monad effects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace, intercept_value
from ._validators import ensure_program_like


@dataclass(frozen=True)
class WriterTellEffect(EffectBase):
    """Appends the message to the writer log without producing a value."""

    message: object

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "WriterTellEffect":
        return self


@dataclass(frozen=True)
class WriterListenEffect(EffectBase):
    """Runs the sub-program and yields a ListenResult of its value and log."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "WriterListenEffect":
        sub_program = intercept_value(self.sub_program, transform)
        if sub_program is self.sub_program:
            return self
        return replace(self, sub_program=sub_program)


def tell(message: object) -> WriterTellEffect:
    return create_effect_with_trace(WriterTellEffect(message=message))


def listen(sub_program: ProgramLike) -> WriterListenEffect:
    return create_effect_with_trace(WriterListenEffect(sub_program=sub_program))


def Tell(message: object) -> Effect:
    return create_effect_with_trace(WriterTellEffect(message=message), skip_frames=3)


def Listen(sub_program: ProgramLike) -> Effect:
    return create_effect_with_trace(WriterListenEffect(sub_program=sub_program), skip_frames=3)


def Log(message: object) -> Effect:
    return create_effect_with_trace(WriterTellEffect(message=message), skip_frames=3)


def slog(**entries: object) -> WriterTellEffect:
    payload = dict(entries)
    return create_effect_with_trace(WriterTellEffect(message=payload))


def StructuredLog(**entries: object) -> Effect:
    payload = dict(entries)
    return create_effect_with_trace(WriterTellEffect(message=payload), skip_frames=3)


__all__ = [
    "WriterTellEffect",
    "WriterListenEffect",
    "tell",
    "listen",
    "Tell",
    "Listen",
    "Log",
    "slog",
    "StructuredLog",
]
