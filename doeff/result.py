"""
Result and Maybe types for doeff.

Result: Ok(value) | Err(error)     — from doeff_vm (Rust)
Maybe:  Some(value) | Nothing      — pure Python

All of them carry their type: ``Some(1).value`` is ``int``, ``Maybe[int]`` is
``Some[int] | Nothing`` and ``Err(ValueError("x")).error`` is ``ValueError``.
Bare ``Some`` / ``Maybe`` / ``Err`` mean ``Some[Any]`` / ``Maybe[Any]`` / ``Err[Any]``,
so code written before they carried a type stays fully known under strict checking.

Usage:
    from doeff import Ok, Err, Some, Nothing, Maybe

    match result:
        case Ok(value): ...
        case Err(error): ...

    def first(items: list[int]) -> Maybe[int]:
        return Some(items[0]) if items else Nothing

    match first(items):
        case Some(value): ...
        case _: ...  # Nothing (a bare ``case Nothing:`` would capture any value)
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final, Generic, Literal, TypeAlias

from doeff_vm import Err, Ok  # noqa: F401 — re-export Rust types

if TYPE_CHECKING:
    # The default (PEP 696) is read by type checkers only: typing.TypeVar takes ``default``
    # from Python 3.13, and typing_extensions is not a runtime dependency of doeff.
    from typing_extensions import TypeVar

    _Value_co = TypeVar("_Value_co", covariant=True, default=Any)
else:
    from typing import TypeVar

    _Value_co = TypeVar("_Value_co", covariant=True)


class Some(Generic[_Value_co]):
    """Maybe with a value. Immutable."""

    __match_args__ = ("value",)
    __slots__ = ("_value",)
    _value: _Value_co

    def __init__(self, value: _Value_co) -> None:
        object.__setattr__(self, "_value", value)

    @property
    def value(self) -> _Value_co:
        return self._value

    def is_some(self) -> Literal[True]:
        return True

    def is_nothing(self) -> Literal[False]:
        return False

    def __repr__(self) -> str:
        return f"Some({self._value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Some) and self._value == other._value

    def __hash__(self) -> int:
        return hash(("Some", self._value))

    def __bool__(self) -> Literal[True]:
        return True

    def __reduce__(self) -> tuple[type["Some[Any]"], tuple[object]]:
        return (Some, (self._value,))


class _NothingType:
    """Maybe without a value. Singleton."""

    __match_args__ = ()
    __slots__ = ()

    def is_some(self) -> Literal[False]:
        return False

    def is_nothing(self) -> Literal[True]:
        return True

    def __repr__(self) -> str:
        return "Nothing"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _NothingType)

    def __hash__(self) -> int:
        return hash("Nothing")

    def __bool__(self) -> Literal[False]:
        return False

    def __reduce__(self) -> tuple[Callable[[], "_NothingType"], tuple[()]]:
        return (_get_nothing, ())


Nothing: Final[_NothingType] = _NothingType()


def _get_nothing() -> _NothingType:
    """Unpickle helper — returns the singleton."""
    return Nothing


Maybe: TypeAlias = Some[_Value_co] | _NothingType
"""``Maybe[T]`` = ``Some[T] | Nothing``; bare ``Maybe`` is ``Maybe[Any]``.

A type for annotations: test a value with ``isinstance(value, Some)`` or ``value is Nothing``
(``isinstance`` refuses a parameterised alias)."""
