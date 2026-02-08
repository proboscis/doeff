"""Public API composition tests aligned to strict SA-008 policy."""

from __future__ import annotations

from typing import Any, cast

import pytest

from doeff import Ask, Get, Program, do
from doeff.rust_vm import default_handlers, run


def test_effects_do_not_expose_python_side_map_flatmap() -> None:
    assert not hasattr(Ask("k"), "map")
    assert not hasattr(Ask("k"), "flat_map")
    assert not hasattr(Get("x"), "map")


def test_kpc_map_returns_rust_map_docontrol() -> None:
    @do
    def identity(x: int):
        if False:
            yield Ask("unused")
        return x

    mapped = identity(1).map(lambda v: v + 1)
    assert type(mapped).__name__ == "Map"


def test_kpc_flat_map_returns_rust_flatmap_docontrol() -> None:
    @do
    def identity(x: int):
        if False:
            yield Ask("unused")
        return x

    chained = identity(1).flat_map(lambda v: cast(Any, Ask(str(v))))
    assert type(chained).__name__ == "FlatMap"


def test_two_gets_returns_tuple_through_run() -> None:
    @do
    def program():
        left = yield Get("x")
        right = yield Get("y")
        return (left, right)

    result = run(program(), handlers=default_handlers(), store={"x": 9, "y": 8})
    assert result.value == (9, 8)


def test_unhandled_effect_raises_typeerror_by_policy() -> None:
    with pytest.raises(TypeError, match=r"UnhandledEffect|unhandled effect"):
        run(Ask("key"), handlers=[])


def test_program_pure_current_runtime_shape() -> None:
    pure = Program.pure(42)
    # current strict runtime keeps Program.pure as effect-like object
    assert type(pure).__name__.endswith("Effect")
