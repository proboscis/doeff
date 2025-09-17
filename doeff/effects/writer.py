"""Writer monad effects."""

from __future__ import annotations

from dataclasses import dataclass

from ._program_types import ProgramLike
from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class WriterTellEffect(EffectBase):
    message: object


@dataclass(frozen=True)
class WriterListenEffect(EffectBase):
    sub_program: ProgramLike


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


__all__ = [
    "WriterTellEffect",
    "WriterListenEffect",
    "tell",
    "listen",
    "Tell",
    "Listen",
    "Log",
]
