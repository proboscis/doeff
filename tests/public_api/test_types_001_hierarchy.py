"""SPEC-TYPES-001 §11.1 — Type Hierarchy Tests (TH-01 through TH-09).

All tests use the public API surface only. No internal imports.
"""

from __future__ import annotations

from doeff import Ask, Get, Program, Put, Tell, do
from doeff.program import (
    DoCtrl,
    DoExpr,
    DoThunk,
    GeneratorProgram,
    KleisliProgramCall,
    ProgramBase,
)
from doeff.types import EffectBase


# ---------------------------------------------------------------------------
# TH-01: DoExpr, DoThunk, DoCtrl are distinct classes
# ---------------------------------------------------------------------------


class TestTH01DistinctTypes:
    def test_doexpr_is_not_dothunk(self) -> None:
        assert DoExpr is not DoThunk

    def test_doexpr_is_not_doctrl(self) -> None:
        assert DoExpr is not DoCtrl

    def test_dothunk_is_not_doctrl(self) -> None:
        assert DoThunk is not DoCtrl


# ---------------------------------------------------------------------------
# TH-02: EffectBase is a subclass of DoExpr
# ---------------------------------------------------------------------------


class TestTH02EffectBaseExtendsDoExpr:
    def test_subclass(self) -> None:
        assert issubclass(EffectBase, DoExpr)

    def test_ask_instance_is_doexpr(self) -> None:
        effect = Ask("key")
        assert isinstance(effect, DoExpr)


# ---------------------------------------------------------------------------
# TH-03: ProgramBase extends DoThunk extends DoExpr
# ---------------------------------------------------------------------------


class TestTH03ProgramBaseChain:
    def test_dothunk_extends_doexpr(self) -> None:
        assert issubclass(DoThunk, DoExpr)

    def test_programbase_extends_dothunk(self) -> None:
        assert issubclass(ProgramBase, DoThunk)

    def test_programbase_extends_doexpr(self) -> None:
        assert issubclass(ProgramBase, DoExpr)


# ---------------------------------------------------------------------------
# TH-04: KleisliProgramCall is EffectBase (not ProgramBase)
# ---------------------------------------------------------------------------


class TestTH04KPCIsEffect:
    def test_kpc_isinstance_effectbase(self) -> None:
        @do
        def add_one(x: int):
            return x + 1

        kpc = add_one(1)
        assert isinstance(kpc, EffectBase)

    def test_kpc_issubclass_effectbase(self) -> None:
        assert issubclass(KleisliProgramCall, EffectBase)


# ---------------------------------------------------------------------------
# TH-05: KPC has no to_generator (not a DoThunk)
# ---------------------------------------------------------------------------


class TestTH05KPCNotThunk:
    def test_no_to_generator(self) -> None:
        @do
        def identity(x: int):
            return x

        kpc = identity(42)
        assert not hasattr(kpc, "to_generator")


# ---------------------------------------------------------------------------
# TH-06: GeneratorProgram is a ProgramBase (DoThunk)
# ---------------------------------------------------------------------------


class TestTH06GeneratorProgramIsThunk:
    def test_is_programbase(self) -> None:
        assert issubclass(GeneratorProgram, ProgramBase)

    def test_is_dothunk(self) -> None:
        assert issubclass(GeneratorProgram, DoThunk)

    def test_instance_has_to_generator(self) -> None:
        prog = GeneratorProgram(lambda: (x for x in [42]))
        assert hasattr(prog, "to_generator")


# ---------------------------------------------------------------------------
# TH-07: Standard effects are EffectBase instances
# ---------------------------------------------------------------------------


class TestTH07EffectsAreEffectBase:
    def test_ask(self) -> None:
        assert isinstance(Ask("key"), EffectBase)

    def test_get(self) -> None:
        assert isinstance(Get("key"), EffectBase)

    def test_put(self) -> None:
        assert isinstance(Put("key", 1), EffectBase)

    def test_tell(self) -> None:
        assert isinstance(Tell("msg"), EffectBase)


# ---------------------------------------------------------------------------
# TH-08: Effects are NOT ProgramBase
# ---------------------------------------------------------------------------


class TestTH08EffectsNotProgramBase:
    def test_ask_not_programbase(self) -> None:
        assert not isinstance(Ask("key"), ProgramBase)

    def test_get_not_programbase(self) -> None:
        assert not isinstance(Get("key"), ProgramBase)

    def test_put_not_programbase(self) -> None:
        assert not isinstance(Put("key", 1), ProgramBase)

    def test_tell_not_programbase(self) -> None:
        assert not isinstance(Tell("msg"), ProgramBase)


# ---------------------------------------------------------------------------
# TH-09: Program is alias for ProgramBase
# ---------------------------------------------------------------------------


class TestTH09ProgramAlias:
    def test_alias(self) -> None:
        assert Program is ProgramBase
