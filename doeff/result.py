"""
Result and Maybe types for doeff.

Result: Ok(value) | Err(error)     — from doeff_vm (Rust)
Maybe:  Some(value) | Nothing      — pure Python

Usage:
    from doeff import Ok, Err, Some, Nothing

    match result:
        case Ok(value): ...
        case Err(error): ...

    match maybe:
        case Some(value): ...
        case Nothing: ...
"""

from doeff_vm import Ok, Err  # noqa: F401 — re-export Rust types


class Some:
    """Maybe with a value. Immutable."""

    __match_args__ = ("value",)
    __slots__ = ("_value",)

    def __init__(self, value):
        object.__setattr__(self, "_value", value)

    @property
    def value(self):
        return self._value

    def is_some(self):
        return True

    def is_nothing(self):
        return False

    def __repr__(self):
        return f"Some({self._value!r})"

    def __eq__(self, other):
        return isinstance(other, Some) and self._value == other._value

    def __hash__(self):
        return hash(("Some", self._value))

    def __bool__(self):
        return True

    def __reduce__(self):
        return (Some, (self._value,))


class _NothingType:
    """Maybe without a value. Singleton."""

    __match_args__ = ()
    __slots__ = ()

    def is_some(self):
        return False

    def is_nothing(self):
        return True

    def __repr__(self):
        return "Nothing"

    def __eq__(self, other):
        return isinstance(other, _NothingType)

    def __hash__(self):
        return hash("Nothing")

    def __bool__(self):
        return False

    def __reduce__(self):
        return (_get_nothing, ())


Nothing = _NothingType()


def _get_nothing():
    """Unpickle helper — returns the singleton."""
    return Nothing


# Maybe is the union type (for type hints)
from typing import Union
Maybe = Union[Some, _NothingType]
