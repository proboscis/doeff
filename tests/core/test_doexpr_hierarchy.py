from __future__ import annotations

import pytest

from doeff import Ask, do
from doeff.program import GeneratorProgram, KleisliProgramCall, ProgramBase


def test_kpc_is_not_thunk_but_is_composable() -> None:
    @do
    def add_one(x: int):
        return x + 1

    kpc = add_one(1)
    assert isinstance(kpc, KleisliProgramCall)
    assert not hasattr(kpc, "to_generator")

    mapped = kpc.map(lambda v: v + 1)
    assert isinstance(mapped, GeneratorProgram)
    assert isinstance(mapped, ProgramBase)


def test_effect_map_and_flat_map_return_program() -> None:
    effect = Ask("token")

    mapped = effect.map(lambda token: str(token).upper())
    assert isinstance(mapped, GeneratorProgram)

    chained = effect.flat_map(lambda token: Ask(str(token)))
    assert isinstance(chained, GeneratorProgram)


def test_effect_flat_map_rejects_non_program_effect() -> None:
    effect = Ask("token")

    program = effect.flat_map(lambda _token: 123)
    gen = program.to_generator()
    next(gen)
    with pytest.raises(TypeError):
        gen.send("abc")
