"""CESK machine error types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doeff.cesk_traceback import CapturedTraceback


@dataclass
class CESKExecutionError(Exception):
    """Wrapper for exceptions that occurred during CESK machine execution.
    
    This provides a clean way to attach CESK traceback information to exceptions
    without monkey-patching the original exception.
    
    Attributes:
        original: The original exception that was raised
        cesk_traceback: The captured CESK stack trace at the time of the error
        store: The store state at the time of the error
    """
    original: BaseException
    cesk_traceback: "CapturedTraceback | None" = None
    store: dict[str, Any] | None = field(default=None, repr=False)
    
    def __str__(self) -> str:
        return str(self.original)
    
    def __repr__(self) -> str:
        return f"CESKExecutionError({self.original!r})"


class HandlerRegistryError(Exception):
    """Raised when there's a conflict or invalid handler registration."""


class UnhandledEffectError(Exception):
    """Raised when no handler exists for an effect."""


class InterpreterInvariantError(Exception):
    """Raised when the interpreter reaches an invalid state."""


class MissingEnvKeyError(KeyError):
    """Raised when Ask effect cannot find the requested key in the environment.

    This error indicates that a required configuration or dependency was not
    provided in the environment. Common causes:
    - Forgot to pass the key in the initial `env` parameter
    - Typo in the key name
    - Missing `Local` wrapper to provide the key

    Attributes:
        key: The environment key that was not found.

    Example:
        >>> @do
        ... def program():
        ...     db = yield Ask("database")  # MissingEnvKeyError if not provided
        ...     return db
        >>>
        >>> # Fix: provide the key in env
        >>> runtime.run(program(), env={"database": my_db})
    """

    def __init__(self, key: Any) -> None:
        self.key = key
        super().__init__(
            f"Environment key not found: {key!r}\n"
            f"Hint: Provide this key via `env={{'{key}': value}}` or wrap with `Local({{'{key}': value}}, ...)`"
        )


__all__ = [
    "CESKExecutionError",
    "HandlerRegistryError",
    "InterpreterInvariantError",
    "MissingEnvKeyError",
    "UnhandledEffectError",
]
