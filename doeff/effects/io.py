"""IO effects."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from .base import Effect, EffectBase, create_effect_with_trace


@dataclass(frozen=True)
class IOPerformEffect(EffectBase):
    """Runs the supplied callable and yields whatever value it returns."""

    action: Callable[[], Any]

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "IOPerformEffect":
        return self


@dataclass(frozen=True)
class IOPrintEffect(EffectBase):
    """Emits a message that will be printed to the active output stream."""

    message: str

    def intercept(
        self, transform: Callable[[Effect], Effect | "Program"]
    ) -> "IOPrintEffect":
        return self


def perform(action: Callable[[], Any]) -> IOPerformEffect:
    return create_effect_with_trace(IOPerformEffect(action=action))


def run(action: Callable[[], Any]) -> IOPerformEffect:
    return perform(action)


def print_(message: str) -> IOPrintEffect:
    return create_effect_with_trace(IOPrintEffect(message=message))


# Uppercase aliases

def IO(action: Callable[[], Any]) -> Effect:
    return create_effect_with_trace(IOPerformEffect(action=action), skip_frames=3)


def Print(message: str) -> Effect:
    return create_effect_with_trace(IOPrintEffect(message=message), skip_frames=3)


__all__ = [
    "IOPerformEffect",
    "IOPrintEffect",
    "IO",
    "Print",
    "perform",
    "print_",
    "run",
]
