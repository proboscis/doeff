from __future__ import annotations

import pytest
from dataclasses import dataclass

from doeff import Ask, Program, do
from doeff.program import KleisliProgramCall
from doeff.rust_vm import default_handlers, run
from doeff.types import EffectBase


def test_kpc_is_not_thunk_but_is_composable() -> None:
    @do
    def add_one(x: int):
        if False:
            yield Ask("unused")
        return x + 1

    kpc = add_one(1)
    assert isinstance(kpc, KleisliProgramCall)
    assert not hasattr(kpc, "to_generator")

    result = run(kpc, handlers=default_handlers())
    assert result.value == 2


def test_effects_do_not_expose_direct_composition_methods() -> None:
    effect = Ask("token")

    assert not hasattr(effect, "map")
    assert not hasattr(effect, "flat_map")


def test_program_lift_wraps_effect_with_perform() -> None:
    lifted = Program.lift(Ask("token"))
    assert type(lifted).__name__ == "Perform"

    result = run(lifted, handlers=default_handlers(), env={"token": "abc"})
    assert result.value == "abc"


@dataclass(frozen=True)
class _LocalEffect(EffectBase):
    value: int


def test_custom_effect_direct_composition_is_deprecated() -> None:
    effect = _LocalEffect(1)

    with pytest.raises(TypeError, match=r"Perform\(effect\)|Program\.lift\(effect\)"):
        effect.map(lambda x: x)
    with pytest.raises(TypeError, match=r"Perform\(effect\)|Program\.lift\(effect\)"):
        effect.flat_map(lambda x: x)
