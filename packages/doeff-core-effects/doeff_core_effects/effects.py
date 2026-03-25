"""
Core effects — Ask, Get, Put, Tell.

These are EffectBase subclasses. Yield them from @do functions.
Handlers (reader, state, writer) handle them.
"""

from doeff_vm import EffectBase


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


class Tell(EffectBase):
    """Writer effect: append a message to the log."""
    def __init__(self, message):
        super().__init__()
        self.message = message

    def __repr__(self):
        return f"Tell({self.message!r})"


class Try(EffectBase):
    """Wrap a program to catch errors as Ok/Err results.

    yield Try(some_program) → Ok(value) or Err(error)
    """
    def __init__(self, program):
        super().__init__()
        self.program = program

    def __repr__(self):
        return f"Try({self.program!r})"


class Slog(EffectBase):
    """Structured log effect: msg + kwargs."""
    def __init__(self, msg, **kwargs):
        super().__init__()
        self.msg = msg
        self.kwargs = kwargs

    def __repr__(self):
        kw = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        if kw:
            return f"Slog({self.msg!r}, {kw})"
        return f"Slog({self.msg!r})"
