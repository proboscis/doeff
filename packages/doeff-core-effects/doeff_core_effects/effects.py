"""Core effects — Ask, Get, Put, Tell, HttpRequest.

These are EffectBase subclasses. Yield them from @do functions.
Handlers (reader, state, writer) handle them.
"""

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import doeff_hy as _doeff_hy  # noqa: F401  # registers Hy import hooks
from doeff_vm import EffectBase

from doeff_core_effects.http_effects import HttpError, HttpRequest, HttpResponse  # noqa: F401

if TYPE_CHECKING:
    from doeff_vm import Err, Ok  # noqa: F401 - named in the string answer type of Try

    from doeff import Program

# Answer types: each effect declares what its handler answers with (EffectBase[T]), so
# ``x = yield from Get("k")`` is typed. Env and state values are dynamic (Any); use
# ``isinstance`` or a typed wrapper at the read site.

_T = TypeVar("_T")


class Ask(EffectBase[Any]):
    """Reader effect: get a value from the environment by key."""

    def __init__(self, key: object) -> None:
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Ask({self.key!r})"


class Get(EffectBase[Any]):
    """State effect: get a value from mutable state by key."""

    def __init__(self, key: object) -> None:
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Get({self.key!r})"


class Put(EffectBase[None]):
    """State effect: set a value in mutable state."""

    def __init__(self, key: object, value: object) -> None:
        super().__init__()
        self.key = key
        self.value = value

    def __repr__(self):
        return f"Put({self.key!r}, {self.value!r})"


def Tell(message: object) -> "WriterTellEffect":  # noqa: N802
    """Convenience: Tell(message) → WriterTellEffect(message)."""
    return WriterTellEffect(message)


class Local(EffectBase[_T], Generic[_T]):
    """Scoped environment injection: run program with overridden env entries.

    yield Local({key: value, ...}, program) → result of program
    """

    def __init__(self, env: dict[Any, Any], program: "Program[_T]") -> None:
        super().__init__()
        self.env = env
        self.program = program

    def __repr__(self):
        return f"Local({self.env!r}, ...)"


class Listen(EffectBase[tuple[_T, list[Any]]], Generic[_T]):
    """Collect all effects of given types emitted during program execution.

    yield Listen(program, types=(WriterTellEffect,)) → (result, collected)
    """

    def __init__(self, program: "Program[_T]", types: tuple[type, ...] | None = None) -> None:
        super().__init__()
        self.program = program
        self.types = types

    def __repr__(self):
        return "Listen(...)"


class Await(EffectBase[_T], Generic[_T]):
    """Await a Python coroutine or future. Bridges async into doeff.

    yield Await(some_coroutine) → result
    """

    def __init__(self, coroutine: Awaitable[_T]) -> None:
        super().__init__()
        self.coroutine = coroutine

    def __repr__(self):
        return "Await(...)"


class Try(EffectBase["Ok[_T] | Err"], Generic[_T]):
    """Wrap a program to catch errors as Ok/Err results.

    yield Try(some_program) → Ok(value) or Err(error)
    """

    def __init__(self, program: "Program[_T]") -> None:
        super().__init__()
        self.program = program

    def __repr__(self):
        return f"Try({self.program!r})"


class WriterTellEffect(EffectBase[None]):
    """Writer effect: a single accumulated message.

    This is the wire type for Tell() only. Listen collects these by default.
    Structured observability logs are SlogEffect, a disjoint wire type
    (ADR-DOE-CORE-EFFECTS-001 R1).
    """

    def __init__(self, msg: object) -> None:
        super().__init__()
        self.msg = msg

    def __repr__(self):
        return f"Tell({self.msg!r})"


class SlogEffect(EffectBase[None]):
    """Structured log (observability) effect: msg + kwargs.

    This is the wire type for slog(). slog_handler displays it on stderr;
    capture flows as values via Listen(prog, types=(SlogEffect,)).
    Not a WriterTellEffect: Writer accumulation and observability have
    opposite default behaviors (ADR-DOE-CORE-EFFECTS-001).
    """

    def __init__(self, msg: object, **kwargs: object) -> None:
        super().__init__()
        self.msg = msg
        self.kwargs = kwargs

    def __repr__(self):
        kw = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        if kw:
            return f"slog({self.msg!r}, {kw})"
        return f"slog({self.msg!r})"


# Convenience alias
Slog = SlogEffect


def slog(msg: object, **kwargs: object) -> SlogEffect:
    """Convenience function to create a SlogEffect."""
    return SlogEffect(msg, **kwargs)
