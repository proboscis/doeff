"""SPEC-TYPES-001 §11.4 + §11.5 — Composition and run() Contract Tests.

CP-01 through CP-08 (composition) and RC-01 through RC-11 (run contract).
"""

from __future__ import annotations

import pytest

from doeff import (
    Ask,
    Err,
    Get,
    Ok,
    Program,
    Put,
    default_handlers,
    do,
    run,
)
from doeff.program import GeneratorProgram, KleisliProgramCall, ProgramBase
from doeff.types import EffectBase


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram."""
    return GeneratorProgram(gen_factory)


# ===========================================================================
# §11.4 — Composition (CP-01 through CP-08)
# ===========================================================================


# ---------------------------------------------------------------------------
# CP-01: effect.map(f) returns GeneratorProgram (DoThunk)
# ---------------------------------------------------------------------------


class TestCP01EffectMap:
    def test_returns_generator_program(self) -> None:
        mapped = Ask("key").map(str.upper)
        assert isinstance(mapped, GeneratorProgram)

    def test_not_effectbase(self) -> None:
        mapped = Ask("key").map(str.upper)
        assert not isinstance(mapped, EffectBase)


# ---------------------------------------------------------------------------
# CP-02: kpc.map(f) returns GeneratorProgram (DoThunk)
# ---------------------------------------------------------------------------


class TestCP02KPCMap:
    def test_returns_generator_program(self) -> None:
        @do
        def identity(x: int):
            return x

        mapped = identity(1).map(lambda v: v + 1)
        assert isinstance(mapped, GeneratorProgram)

    def test_not_kpc(self) -> None:
        @do
        def identity(x: int):
            return x

        mapped = identity(1).map(lambda v: v + 1)
        assert not isinstance(mapped, KleisliProgramCall)


# ---------------------------------------------------------------------------
# CP-03: effect.flat_map(f) returns GeneratorProgram
# ---------------------------------------------------------------------------


class TestCP03FlatMap:
    def test_returns_generator_program(self) -> None:
        chained = Ask("key").flat_map(lambda v: Ask(str(v)))
        assert isinstance(chained, GeneratorProgram)


# ---------------------------------------------------------------------------
# CP-04: Composed effect runs end-to-end through run()
# ---------------------------------------------------------------------------


class TestCP04ComposedEffectE2E:
    def test_ask_map_runs(self) -> None:
        mapped = Ask("key").map(str.upper)
        result = run(mapped, handlers=default_handlers(), env={"key": "hello"})
        assert result.value == "HELLO"

    def test_get_map_runs(self) -> None:
        mapped = Get("x").map(lambda v: v * 2)
        result = run(mapped, handlers=default_handlers(), store={"x": 21})
        assert result.value == 42


# ---------------------------------------------------------------------------
# CP-05: Composed KPC runs end-to-end through run()
# ---------------------------------------------------------------------------


class TestCP05ComposedKPCE2E:
    def test_kpc_map_runs(self) -> None:
        @do
        def get_value(key: str):
            val = yield Ask(key)
            return val

        mapped = get_value("name").map(str.upper)
        result = run(mapped, handlers=default_handlers(), env={"name": "alice"})
        assert result.value == "ALICE"


# ---------------------------------------------------------------------------
# CP-06: Chained composition
# ---------------------------------------------------------------------------


class TestCP06ChainedMap:
    def test_double_map(self) -> None:
        prog = Ask("x").map(int).map(lambda v: v * 2)
        result = run(prog, handlers=default_handlers(), env={"x": "21"})
        assert result.value == 42


# ---------------------------------------------------------------------------
# CP-07: flat_map rejects non-Program binder
# ---------------------------------------------------------------------------


class TestCP07FlatMapRejectsNonProgram:
    def test_rejects_plain_value(self) -> None:
        prog = Ask("key").flat_map(lambda _: 123)
        gen = prog.to_generator()
        next(gen)
        with pytest.raises(TypeError):
            gen.send("abc")


# ---------------------------------------------------------------------------
# CP-08: Program.pure(value)
# ---------------------------------------------------------------------------


class TestCP08ProgramPure:
    def test_pure_creates_program(self) -> None:
        prog = Program.pure(42)
        assert isinstance(prog, ProgramBase)

    def test_pure_runs(self) -> None:
        result = run(Program.pure(42), handlers=default_handlers())
        assert result.value == 42


# ===========================================================================
# §11.5 — run() Contract (RC-01 through RC-11)
# ===========================================================================


# ---------------------------------------------------------------------------
# RC-01: run() with no handlers raises unhandled
# ---------------------------------------------------------------------------


class TestRC01NoHandlers:
    def test_effect_without_handler_fails(self) -> None:
        def gen():
            yield Get("x")
            return 1

        result = run(_prog(gen), store={"x": 0})
        assert result.is_err()


# ---------------------------------------------------------------------------
# RC-02: run() with default_handlers installs state+reader+writer
# ---------------------------------------------------------------------------


class TestRC02DefaultHandlers:
    def test_state_reader_writer_work(self) -> None:
        @do
        def main():
            env_val = yield Ask("key")
            state_val = yield Get("counter")
            yield Put("counter", state_val + 1)
            return f"{env_val}:{state_val}"

        result = run(
            main(),
            handlers=default_handlers(),
            env={"key": "test"},
            store={"counter": 10},
        )
        assert result.value == "test:10"
        assert result.raw_store["counter"] == 11


# ---------------------------------------------------------------------------
# RC-03: RunResult.result returns Ok or Err
# ---------------------------------------------------------------------------


class TestRC03ResultType:
    def test_success_returns_ok(self) -> None:
        result = run(Program.pure(42), handlers=default_handlers())
        assert isinstance(result.result, Ok)

    def test_failure_returns_err(self) -> None:
        def gen():
            raise ValueError("boom")
            yield  # noqa: RET504

        result = run(_prog(gen), handlers=default_handlers())
        assert isinstance(result.result, Err)


# ---------------------------------------------------------------------------
# RC-04/RC-05: isinstance checks for Ok/Err
# ---------------------------------------------------------------------------


class TestRC04RC05IsInstance:
    def test_ok_isinstance(self) -> None:
        result = run(Program.pure(42), handlers=default_handlers())
        assert isinstance(result.result, Ok)

    def test_err_isinstance(self) -> None:
        def gen():
            raise ValueError("fail")
            yield

        result = run(_prog(gen), handlers=default_handlers())
        assert isinstance(result.result, Err)


# ---------------------------------------------------------------------------
# RC-06: RunResult.value extracts success value
# ---------------------------------------------------------------------------


class TestRC06Value:
    def test_value(self) -> None:
        result = run(Program.pure(42), handlers=default_handlers())
        assert result.value == 42


# ---------------------------------------------------------------------------
# RC-07: RunResult.raw_store reflects final state
# ---------------------------------------------------------------------------


class TestRC07RawStore:
    def test_store_updated(self) -> None:
        def gen():
            yield Put("x", 99)
            return "done"

        result = run(_prog(gen), handlers=default_handlers(), store={"x": 0})
        assert result.raw_store["x"] == 99


# ---------------------------------------------------------------------------
# RC-08: RunResult.error returns exception for failures
# ---------------------------------------------------------------------------


class TestRC08Error:
    def test_error_property(self) -> None:
        def gen():
            raise ValueError("boom")
            yield

        result = run(_prog(gen), handlers=default_handlers())
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "boom"


# ---------------------------------------------------------------------------
# RC-09: Import paths for dispatch primitives
# ---------------------------------------------------------------------------


class TestRC09ImportPrimitives:
    def test_from_doeff(self) -> None:
        from doeff import Delegate, K, Resume, Transfer, WithHandler, async_run, run

        for obj in (run, async_run, WithHandler, Resume, Delegate, Transfer, K):
            assert obj is not None

    def test_identity_with_doeff_vm(self) -> None:
        import doeff
        import doeff_vm

        assert doeff.WithHandler is doeff_vm.WithHandler
        assert doeff.Resume is doeff_vm.Resume
        assert doeff.Delegate is doeff_vm.Delegate
        assert doeff.Transfer is doeff_vm.Transfer
        assert doeff.K is doeff_vm.K


# ---------------------------------------------------------------------------
# RC-10: Import paths for handler sentinels
# ---------------------------------------------------------------------------


class TestRC10ImportHandlers:
    def test_from_doeff_handlers(self) -> None:
        from doeff.handlers import reader, scheduler, state, writer

        for obj in (state, reader, writer, scheduler):
            assert obj is not None

    def test_identity_with_doeff_vm(self) -> None:
        import doeff_vm
        from doeff.handlers import reader, state, writer

        assert state is doeff_vm.state
        assert reader is doeff_vm.reader
        assert writer is doeff_vm.writer


# ---------------------------------------------------------------------------
# RC-11: Import paths for presets
# ---------------------------------------------------------------------------


class TestRC11ImportPresets:
    def test_from_doeff_presets(self) -> None:
        from doeff.presets import async_preset, sync_preset

        assert isinstance(sync_preset, list)
        assert isinstance(async_preset, list)

    def test_sync_preset_is_default_handlers(self) -> None:
        from doeff.presets import sync_preset

        defaults = default_handlers()
        assert len(sync_preset) == len(defaults)

    def test_async_preset_includes_scheduler(self) -> None:
        from doeff.presets import async_preset, sync_preset

        assert len(async_preset) > len(sync_preset)
