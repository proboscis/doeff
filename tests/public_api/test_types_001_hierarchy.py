"""SPEC-TYPES-001 hierarchy tests (post-DoThunk removal)."""

from __future__ import annotations

from doeff import Ask, Get, Perform, Program, Put, Tell, do
from doeff.program import DoCtrl, DoExpr, GeneratorProgram, KleisliProgramCall, ProgramBase
from doeff.types import EffectBase


class TestTH01DistinctTypes:
    def test_doexpr_is_not_doctrl(self) -> None:
        assert DoExpr is not DoCtrl


class TestTH02EffectBaseExtendsDoExpr:
    def test_subclass(self) -> None:
        assert not issubclass(EffectBase, DoExpr)

    def test_ask_instance_is_doexpr(self) -> None:
        assert not isinstance(Ask("key"), DoExpr)

    def test_perform_lifts_effect_to_doexpr(self) -> None:
        import doeff_vm

        assert isinstance(Perform(Ask("key")), doeff_vm.DoExpr)


class TestTH03ProgramBaseChain:
    def test_programbase_extends_doexpr(self) -> None:
        assert issubclass(ProgramBase, DoExpr)


class TestTH04KPCIsEffect:
    def test_kpc_isinstance_effectbase(self) -> None:
        @do
        def add_one(x: int):
            if False:
                yield Ask("unused")
            return x + 1

        assert isinstance(add_one(1), EffectBase)

    def test_kpc_issubclass_effectbase(self) -> None:
        assert issubclass(KleisliProgramCall, EffectBase)


class TestTH05KPCNotThunk:
    def test_no_to_generator(self) -> None:
        @do
        def identity(x: int):
            if False:
                yield Ask("unused")
            return x

        assert not hasattr(identity(42), "to_generator")


class TestTH06GeneratorProgramShape:
    def test_is_programbase(self) -> None:
        assert issubclass(GeneratorProgram, ProgramBase)

    def test_instance_has_to_generator(self) -> None:
        def gen():
            if False:
                yield Ask("unused")
            return 42

        prog = GeneratorProgram(gen)
        assert hasattr(prog, "to_generator")


class TestTH07EffectsAreEffectBase:
    def test_standard_effects(self) -> None:
        for effect in (Ask("key"), Get("key"), Put("key", 1), Tell("msg")):
            assert isinstance(effect, EffectBase)


class TestTH08EffectsNotProgramBase:
    def test_standard_effects_not_programbase(self) -> None:
        for effect in (Ask("key"), Get("key"), Put("key", 1), Tell("msg")):
            assert not isinstance(effect, ProgramBase)


class TestTH09ProgramAlias:
    def test_alias(self) -> None:
        assert Program is ProgramBase
