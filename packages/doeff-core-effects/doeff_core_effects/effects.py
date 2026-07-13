"""Core effects — Ask, Get, Put, Tell, HttpRequest.

These are EffectBase subclasses. Yield them from @do functions.
Handlers (reader, state, writer) handle them.
"""

import doeff_hy as _doeff_hy  # noqa: F401  # registers Hy import hooks
from doeff_vm import EffectBase

from doeff_core_effects.http_effects import HttpError, HttpRequest, HttpResponse  # noqa: F401


class Ask(EffectBase):
    """Reader effect: get a value from the environment by key."""

    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Ask({self.key!r})"


class Get(EffectBase):
    """State effect: get a value from mutable state by key."""

    def __init__(self, key):
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Get({self.key!r})"


class Put(EffectBase):
    """State effect: set a value in mutable state."""

    def __init__(self, key, value):
        super().__init__()
        self.key = key
        self.value = value

    def __repr__(self):
        return f"Put({self.key!r}, {self.value!r})"


def Tell(message):  # noqa: N802
    """Convenience: Tell(message) → WriterTellEffect(message)."""
    return WriterTellEffect(message)


class Local(EffectBase):
    """Scoped environment injection: run program with overridden env entries.

    yield Local({key: value, ...}, program) → result of program
    """

    def __init__(self, env, program):
        super().__init__()
        self.env = env
        self.program = program

    def __repr__(self):
        return f"Local({self.env!r}, ...)"


class Listen(EffectBase):
    """Collect all effects of given types emitted during program execution.

    yield Listen(program, types=(WriterTellEffect,)) → (result, collected)
    """

    def __init__(self, program, types=None):
        super().__init__()
        self.program = program
        self.types = types

    def __repr__(self):
        return "Listen(...)"


class Await(EffectBase):
    """Await a Python coroutine or future. Bridges async into doeff.

    yield Await(some_coroutine) → result
    """

    def __init__(self, coroutine):
        super().__init__()
        self.coroutine = coroutine

    def __repr__(self):
        return "Await(...)"


class Try(EffectBase):
    """Wrap a program to catch errors as Ok/Err results.

    yield Try(some_program) → Ok(value) or Err(error)
    """

    def __init__(self, program):
        super().__init__()
        self.program = program

    def __repr__(self):
        return f"Try({self.program!r})"


class WriterTellEffect(EffectBase):
    """Writer effect: a single accumulated message.

    This is the wire type for Tell() only. Listen collects these by default.
    Structured observability logs are SlogEffect, a disjoint wire type
    (ADR-DOE-CORE-EFFECTS-001 R1).
    """

    def __init__(self, msg):
        super().__init__()
        self.msg = msg

    def __repr__(self):
        return f"Tell({self.msg!r})"


class SlogEffect(EffectBase):
    """Structured log (observability) effect: msg + kwargs.

    This is the wire type for slog(). slog_handler() displays it on stderr;
    capture flows as values via Listen(prog, types=(SlogEffect,)).
    Not a WriterTellEffect: Writer accumulation and observability have
    opposite default behaviors (ADR-DOE-CORE-EFFECTS-001).
    """

    def __init__(self, msg, **kwargs):
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


def slog(msg, **kwargs):
    """Convenience function to create a SlogEffect."""
    return SlogEffect(msg, **kwargs)
