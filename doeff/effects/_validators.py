"""Runtime validators for effect attribute type checking."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable, Mapping
import inspect
from typing import Any, TYPE_CHECKING

from doeff.types import EffectBase

if TYPE_CHECKING:
    from doeff.program import Program, KleisliProgramCall


def _type_name(value: object) -> str:
    return type(value).__name__


def _is_program_like(value: object) -> bool:
    """Check if value is Program, KleisliProgramCall, or Effect."""
    if isinstance(value, EffectBase):
        return True
    # Import here to avoid circular imports
    from doeff.program import Program, KleisliProgramCall
    return isinstance(value, (Program, KleisliProgramCall))


def ensure_str(value: object, *, name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {_type_name(value)}")


def ensure_hashable(value: object, *, name: str) -> None:
    if not isinstance(value, Hashable):
        raise TypeError(f"{name} must be hashable, got {_type_name(value)}")
    try:
        hash(value)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise TypeError(f"{name} must be hashable, got {_type_name(value)}") from exc


def ensure_callable(value: object, *, name: str) -> None:
    if not callable(value):
        raise TypeError(f"{name} must be callable, got {_type_name(value)}")


def ensure_optional_callable(value: object | None, *, name: str) -> None:
    if value is not None and not callable(value):
        raise TypeError(f"{name} must be callable or None, got {_type_name(value)}")


def ensure_program_like(value: object, *, name: str) -> None:
    if not _is_program_like(value):
        raise TypeError(f"{name} must be Program or Effect, got {_type_name(value)}")


def ensure_program_like_or_thunk(value: object, *, name: str) -> None:
    if _is_program_like(value):
        return
    if callable(value):
        try:
            inspect.signature(value).bind()
        except TypeError as exc:
            raise TypeError(
                f"{name} callable must accept no required arguments"
            ) from exc
        except ValueError:
            # Unable to introspect (e.g., builtins); assume callable accepts zero args.
            pass
        return
    raise TypeError(
        f"{name} must be Program, Effect, or zero-argument callable, got {_type_name(value)}"
    )


def ensure_program_tuple(values: object, *, name: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be tuple, got {_type_name(values)}")
    for index, item in enumerate(values):
        ensure_program_like(item, name=f"{name}[{index}]")


def ensure_program_mapping(values: object, *, name: str) -> None:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be mapping, got {_type_name(values)}")
    for key, item in values.items():
        if not isinstance(key, str):
            raise TypeError(f"{name} keys must be str, got {_type_name(key)}")
        ensure_program_like(item, name=f"{name}['{key}']")


def ensure_env_mapping(values: object, *, name: str) -> None:
    if not isinstance(values, Mapping):
        raise TypeError(f"{name} must be mapping, got {_type_name(values)}")
    for key in values.keys():
        ensure_hashable(key, name=f"{name} key")


def ensure_dict_str_any(value: object, *, name: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be dict, got {_type_name(value)}")
    for key in value.keys():
        if not isinstance(key, str):
            raise TypeError(f"{name} keys must be str, got {_type_name(key)}")


def ensure_awaitable(value: object, *, name: str) -> None:
    if not isinstance(value, Awaitable):
        raise TypeError(f"{name} must be Awaitable, got {_type_name(value)}")


def ensure_awaitable_tuple(values: object, *, name: str) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be tuple, got {_type_name(values)}")
    for index, item in enumerate(values):
        ensure_awaitable(item, name=f"{name}[{index}]")


def ensure_exception(value: object, *, name: str) -> None:
    if not isinstance(value, Exception):
        raise TypeError(f"{name} must be Exception instance, got {_type_name(value)}")


def ensure_positive_int(value: object, *, name: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise TypeError(f"{name} must be positive int, got {_type_name(value)}={value!r}")


def ensure_non_negative_int(value: object, *, name: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise TypeError(f"{name} must be non-negative int, got {_type_name(value)}={value!r}")


def ensure_non_empty_tuple(values: object, *, name: str) -> None:
    ensure_program_tuple(values, name=name)
    if not values:
        raise ValueError(f"{name} must not be empty")


__all__ = [
    "ensure_str",
    "ensure_hashable",
    "ensure_callable",
    "ensure_optional_callable",
    "ensure_program_like",
    "ensure_program_like_or_thunk",
    "ensure_program_tuple",
    "ensure_program_mapping",
    "ensure_env_mapping",
    "ensure_dict_str_any",
    "ensure_awaitable",
    "ensure_awaitable_tuple",
    "ensure_exception",
    "ensure_positive_int",
    "ensure_non_negative_int",
    "ensure_non_empty_tuple",
]
