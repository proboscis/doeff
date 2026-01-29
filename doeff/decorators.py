"""Helper decorators for declaring doeff-specific protocols."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable[..., object])


def do_wrapper(factory: F) -> F:
    """Mark ``factory`` as returning decorators that wrap @do programs.

    Decorator factories annotated with ``@do_wrapper`` signal to tooling that the
    decorator they produce keeps the wrapped @do function's semantics intact. Static
    analyzers can treat uses of the resulting decorator as transparent when
    tracing dependencies.
    """

    factory.__doeff_do_wrapper__ = True
    return factory


__all__ = ["do_wrapper"]
