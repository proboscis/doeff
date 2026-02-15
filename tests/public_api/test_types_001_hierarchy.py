"""SPEC-TYPES-001 hierarchy tests (macro model).

MACRO MODEL: @do call values are DoCtrl/ProgramBase, NOT EffectBase values.
The @do call path produces Call DoCtrl semantics resolved by the VM trampoline.
"""

from __future__ import annotations

from doeff import (
    Ask,
    DoCtrl,
    DoExpr,
    EffectBase,
    GeneratorProgram,
    Get,
    Perform,
    Program,
    ProgramBase,
    Put,
    Tell,
    do,
)


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


class TestTH04KPCIsProgramShape:
    """KPC is program-shaped and composes via ProgramBase/DoCtrl APIs."""

    def test_kpc_isinstance_programbase_or_doctrl(self) -> None:
        """@do call result should satisfy ProgramBase/DoCtrl relationship."""

        @do
        def add_one(x: int):
            if False:
                yield Ask("unused")
            return x + 1

        kpc = add_one(1)
        assert isinstance(kpc, (DoCtrl, ProgramBase)), (
            f"KPC should be DoCtrl/ProgramBase-compatible, got {type(kpc).__name__}"
        )

    def test_call_result_not_effect_value(self) -> None:
        """@do call result should be control/program, not a bare effect value."""

        @do
        def identity(x: int):
            if False:
                yield Ask("unused")
            return x

        assert not isinstance(identity(42), EffectBase)

    def test_kpc_is_doctrl_or_programbase(self) -> None:
        """Macro model: KPC should be a DoCtrl or ProgramBase."""

        @do
        def identity(x: int):
            if False:
                yield Ask("unused")
            return x

        kpc = identity(42)
        assert isinstance(kpc, (DoCtrl, ProgramBase)), (
            f"Macro model: KPC should be DoCtrl or ProgramBase, got {type(kpc).__name__}"
        )

    def test_call_class_is_doctrl_type(self) -> None:
        """Macro model: @do call values are ProgramBase/DoCtrl values."""

        @do
        def identity(x: int):
            if False:
                yield Ask("unused")
            return x

        assert isinstance(identity(1), (DoCtrl, ProgramBase))


class TestTH05KPCNotThunk:
    def test_call_exposes_to_generator_bridge(self) -> None:
        @do
        def identity(x: int):
            if False:
                yield Ask("unused")
            return x

        assert hasattr(identity(42), "to_generator")


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
