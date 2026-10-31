"""``Annotated`` contract types run as the type they annotate.

A contract may name a type that carries a mark for the static reader (doeff's ``BuiltBy`` —
``Annotated[Callable[..., object], BuiltBy(f)]``). At run time the check is the annotated type's.
"""

from collections.abc import Callable
from typing import Annotated

import pytest
from doeff.handler_values import BuiltBy
from doeff_hy._type_forms import runtime_class


def _factory() -> None:
    return None


def test_an_annotated_type_checks_as_the_type_it_annotates() -> None:
    marked = Annotated[Callable[..., object], BuiltBy(_factory)]

    assert runtime_class(marked) is Callable
    assert runtime_class(Annotated[int, "mark"] | None) == (int, type(None))


def test_built_by_needs_a_factory() -> None:
    with pytest.raises(ValueError, match="at least one factory"):
        BuiltBy()
