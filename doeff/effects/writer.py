"""Writer monad effects (Rust-backed core tell effect)."""

from __future__ import annotations

from dataclasses import dataclass

import doeff_vm
from doeff.types import ListenResult
from doeff.utils import BoundedLog

from ._program_types import ProgramLike
from ._validators import ensure_program_like
from .base import Effect, EffectBase


WriterTellEffect = doeff_vm.PyTell


@dataclass(frozen=True)
class WriterListenEffect(EffectBase):
    """Runs the sub-program and yields a ListenResult of its value and log."""

    sub_program: ProgramLike

    def __post_init__(self) -> None:
        ensure_program_like(self.sub_program, name="sub_program")


def tell(message: object) -> WriterTellEffect:
    return WriterTellEffect(message)


def listen(sub_program: ProgramLike):
    ensure_program_like(sub_program, name="sub_program")

    from doeff import do

    @do
    def _listen_program():
        captured = BoundedLog()

        def handle_listen_tell(effect, k):
            if isinstance(effect, WriterTellEffect):
                captured.append(effect.message)
                return (yield doeff_vm.Resume(k, None))
            return (yield doeff_vm.Delegate())

        value = yield doeff_vm.WithHandler(handle_listen_tell, sub_program)
        return ListenResult(value=value, log=captured)

    return _listen_program()


def Tell(message: object) -> Effect:
    return WriterTellEffect(message)


# Log is an alias for Tell - commonly used in documentation
Log = Tell


def Listen(sub_program: ProgramLike) -> Effect:
    ensure_program_like(sub_program, name="sub_program")

    from doeff import do

    @do
    def _listen_program():
        captured = BoundedLog()

        def handle_listen_tell(effect, k):
            if isinstance(effect, WriterTellEffect):
                captured.append(effect.message)
                return (yield doeff_vm.Resume(k, None))
            return (yield doeff_vm.Delegate())

        value = yield doeff_vm.WithHandler(handle_listen_tell, sub_program)
        return ListenResult(value=value, log=captured)

    return _listen_program()


def slog(**entries: object) -> WriterTellEffect:
    payload = dict(entries)
    return WriterTellEffect(payload)


def StructuredLog(**entries: object) -> Effect:
    payload = dict(entries)
    return WriterTellEffect(payload)


__all__ = [
    "Listen",
    "Log",
    "StructuredLog",
    "Tell",
    "WriterListenEffect",
    "WriterTellEffect",
    "listen",
    "slog",
    "tell",
]
