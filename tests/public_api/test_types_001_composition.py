"""Public API composition tests aligned to strict SA-008 policy."""

from __future__ import annotations

import pytest
import doeff_vm

from doeff import Ask, Get, Program, default_handlers, do, run


def test_effects_do_not_expose_python_side_map_flatmap() -> None:
    assert not hasattr(Ask("k"), "map")
    assert not hasattr(Ask("k"), "flat_map")
    assert not hasattr(Get("x"), "map")


def test_kpc_does_not_expose_effect_level_map_or_flat_map() -> None:
    @do
    def identity(x: int):
        if False:
            yield Ask("unused")
        return x

    kpc = identity(1)
    assert not hasattr(kpc, "map")
    assert not hasattr(kpc, "flat_map")


def test_kpc_composition_uses_lowered_control_path() -> None:
    @do
    def identity(x: int):
        if False:
            yield Ask("unused")
        return x

    @do
    def composed():
        base = yield identity(1)
        env = yield Ask("suffix")
        return f"{base}:{env}"

    result = run(composed(), handlers=default_handlers(), env={"suffix": "ok"})
    assert result.value == "1:ok"


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
    # Program.pure lowers to Rust DoCtrl `Pure`.
    assert type(pure).__name__ == "Pure"
    assert isinstance(pure, doeff_vm.DoExpr)
