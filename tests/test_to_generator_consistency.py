"""Test that to_generator return types are consistent across all Program types."""

import inspect
from collections.abc import Generator
from typing import Any

import pytest

from doeff import do
from doeff.program import (
    GeneratorProgram,
    KleisliProgramCall,
    Program,
    ProgramBase,
    _InterceptedProgram,
)
from doeff.effects import ask, local
from doeff.effects.pure import PureEffect, Pure
from doeff.effects.reader import LocalEffect
from doeff.types import Effect, EffectBase


class TestToGeneratorReturnTypes:
    """Verify to_generator returns Generator, not GeneratorProgram."""

    def test_generator_program_to_generator_returns_generator(self) -> None:
        """GeneratorProgram.to_generator should return a Generator."""

        def factory() -> Generator[Effect, Any, int]:
            yield Pure(42)
            return 42

        prog = GeneratorProgram(factory)
        result = prog.to_generator()

        assert isinstance(result, Generator), (
            f"GeneratorProgram.to_generator() returned {type(result).__name__}, "
            "expected Generator"
        )
        assert not isinstance(result, GeneratorProgram), (
            "GeneratorProgram.to_generator() should return Generator, not GeneratorProgram"
        )

    def test_kleisli_program_call_to_generator_returns_generator(self) -> None:
        """KleisliProgramCall.to_generator should return a Generator."""

        @do
        def my_program() -> Generator[Effect, Any, int]:
            x = yield Pure(42)
            return x

        prog = my_program()
        assert isinstance(prog, KleisliProgramCall)

        result = prog.to_generator()

        assert isinstance(result, Generator), (
            f"KleisliProgramCall.to_generator() returned {type(result).__name__}, "
            "expected Generator"
        )
        assert not isinstance(result, GeneratorProgram), (
            "KleisliProgramCall.to_generator() should return Generator, not GeneratorProgram"
        )

    def test_intercepted_program_to_generator_returns_generator(self) -> None:
        """_InterceptedProgram.to_generator should return a Generator."""

        @do
        def my_program() -> Generator[Effect, Any, int]:
            x = yield Pure(42)
            return x

        prog = my_program().intercept(lambda e: e)
        assert isinstance(prog, _InterceptedProgram)

        result = prog.to_generator()

        assert isinstance(result, Generator), (
            f"_InterceptedProgram.to_generator() returned {type(result).__name__}, "
            "expected Generator"
        )
        assert not isinstance(result, GeneratorProgram), (
            "_InterceptedProgram.to_generator() should return Generator, not GeneratorProgram"
        )


class TestEffectToGenerator:
    """
    Test that Effects correctly implement to_generator.

    EffectBase.to_generator() returns a Generator that yields the effect itself
    and returns the handled result. This allows uniform handling of Effects and
    Programs by interpreters.
    """

    def test_ask_effect_to_generator_returns_generator(self) -> None:
        """AskEffect.to_generator should return a proper Generator."""
        effect = ask("key")
        assert isinstance(effect, EffectBase)

        assert hasattr(effect, "to_generator"), (
            "Effect should have to_generator method"
        )

        result = effect.to_generator()

        # Should return a Generator, not GeneratorProgram
        assert isinstance(result, Generator), (
            f"Expected Generator, got {type(result).__name__}"
        )
        assert not isinstance(result, GeneratorProgram), (
            "Should return Generator, not GeneratorProgram"
        )

    def test_pure_effect_to_generator_returns_generator(self) -> None:
        """PureEffect.to_generator should return a proper Generator."""
        effect = Pure(42)
        assert isinstance(effect, EffectBase)

        result = effect.to_generator()

        assert isinstance(result, Generator), (
            f"Expected Generator, got {type(result).__name__}"
        )
        assert not isinstance(result, GeneratorProgram), (
            "Should return Generator, not GeneratorProgram"
        )

    def test_local_effect_to_generator_returns_generator(self) -> None:
        """LocalEffect.to_generator should return a proper Generator."""
        effect = local({"key": "value"}, Pure(42))
        assert isinstance(effect, LocalEffect)

        result = effect.to_generator()

        assert isinstance(result, Generator), (
            f"Expected Generator, got {type(result).__name__}"
        )
        assert not isinstance(result, GeneratorProgram), (
            "Should return Generator, not GeneratorProgram"
        )


class TestProgramBaseGetattr:
    """Test ProgramBase.__getattr__ behavior."""

    def test_getattr_creates_projection_program(self) -> None:
        """__getattr__ creates a program that projects attributes from result."""

        @do
        def my_program() -> Generator[Any, Any, dict[str, Any]]:
            return {"name": "test", "value": 42}

        prog = my_program()
        # Accessing .name creates a projection program
        name_prog = prog.name

        assert isinstance(name_prog, (GeneratorProgram, KleisliProgramCall))

    def test_getattr_does_not_catch_to_generator(self) -> None:
        """
        __getattr__ should not intercept to_generator since it's now a real method.

        EffectBase now defines to_generator(), so __getattr__ is not invoked
        when accessing effect.to_generator.
        """
        effect = ask("key")

        # to_generator should be a bound method, not a projection program
        to_gen = effect.to_generator

        # Should be a callable method, not a ProgramBase
        assert callable(to_gen), "to_generator should be callable"
        assert not isinstance(to_gen, ProgramBase), (
            "to_generator should be a method, not a projection program"
        )


class TestToGeneratorSignatures:
    """Test that all to_generator methods have consistent signatures."""

    def test_all_program_subclasses_have_to_generator(self) -> None:
        """All concrete Program subclasses should have to_generator."""
        concrete_classes = [GeneratorProgram, KleisliProgramCall, _InterceptedProgram]

        for cls in concrete_classes:
            assert hasattr(cls, "to_generator"), f"{cls.__name__} missing to_generator"
            method = getattr(cls, "to_generator")
            sig = inspect.signature(method)
            # Should only have self parameter
            params = list(sig.parameters.keys())
            assert params == ["self"], (
                f"{cls.__name__}.to_generator has unexpected params: {params}"
            )


class TestToGeneratorBehavior:
    """Test to_generator behavior across different program types."""

    def test_generator_program_factory_called_each_time(self) -> None:
        """Each to_generator call should create a fresh generator."""
        call_count = 0

        def factory() -> Generator[Effect, Any, int]:
            nonlocal call_count
            call_count += 1
            yield Pure(call_count)
            return call_count

        prog = GeneratorProgram(factory)

        gen1 = prog.to_generator()
        gen2 = prog.to_generator()

        assert gen1 is not gen2, "to_generator should return new generator each call"
        # Factory is called when generator is created (to_generator returns factory())
        # Need to consume the generators to trigger the count
        next(gen1)  # Triggers factory execution
        next(gen2)  # Triggers factory execution
        assert call_count == 2, "Factory should be called for each to_generator"

    def test_kleisli_program_creates_fresh_generator(self) -> None:
        """KleisliProgramCall.to_generator should create fresh generators."""

        @do
        def my_program() -> Generator[Effect, Any, int]:
            x = yield Pure(42)
            return x

        prog = my_program()

        gen1 = prog.to_generator()
        gen2 = prog.to_generator()

        assert gen1 is not gen2, "to_generator should return new generator each call"

    def test_mapped_program_to_generator_returns_generator(self) -> None:
        """Mapped programs should return Generators from to_generator."""

        @do
        def my_program() -> Generator[Any, Any, int]:
            return 42

        prog = my_program().map(lambda x: x * 2)
        # map on KleisliProgramCall returns KleisliProgramCall
        assert isinstance(prog, (GeneratorProgram, KleisliProgramCall))

        result = prog.to_generator()
        assert isinstance(result, Generator), (
            f"Mapped program.to_generator() returned {type(result).__name__}"
        )

    def test_flat_mapped_program_to_generator_returns_generator(self) -> None:
        """Flat-mapped programs should return Generators from to_generator."""

        @do
        def my_program() -> Generator[Any, Any, int]:
            return 42

        @do
        def next_program(x: int) -> Generator[Any, Any, int]:
            return x * 2

        prog = my_program().flat_map(next_program)
        assert isinstance(prog, (GeneratorProgram, KleisliProgramCall))

        result = prog.to_generator()
        assert isinstance(result, Generator), (
            f"Flat-mapped program.to_generator() returned {type(result).__name__}"
        )


class TestLocalEffectSubProgram:
    """Specific tests for LocalEffect sub_program handling."""

    def test_local_effect_with_generator_program(self) -> None:
        """LocalEffect can wrap GeneratorProgram."""

        def factory() -> Generator[Effect, Any, int]:
            yield Pure(42)
            return 42

        gp = GeneratorProgram(factory)
        effect = local({"key": "value"}, gp)

        assert effect.sub_program is gp
        assert isinstance(effect.sub_program, GeneratorProgram)

    def test_local_effect_with_kleisli_program_call(self) -> None:
        """LocalEffect can wrap KleisliProgramCall."""

        @do
        def my_program() -> Generator[Any, Any, int]:
            return 42

        kpc = my_program()
        effect = local({"key": "value"}, kpc)

        assert effect.sub_program is kpc
        assert isinstance(effect.sub_program, KleisliProgramCall)

    def test_local_effect_with_effect(self) -> None:
        """LocalEffect can wrap Effect."""
        inner = Pure(42)
        effect = local({"key": "value"}, inner)

        assert effect.sub_program is inner
        assert isinstance(effect.sub_program, EffectBase)


class TestProgramBaseNoToGenerator:
    """Verify ProgramBase doesn't define to_generator (subclasses do)."""

    def test_program_base_to_generator_not_in_dict(self) -> None:
        """ProgramBase should not define to_generator in its __dict__."""
        assert "to_generator" not in ProgramBase.__dict__, (
            "ProgramBase should not define to_generator - subclasses should"
        )


class TestEffectBaseToGeneratorFix:
    """Tests verifying the fix: EffectBase now has to_generator method."""

    def test_effect_has_to_generator_method(self) -> None:
        """
        Effects should have to_generator as a proper method.

        This allows uniform handling of Effects and Programs by interpreters.
        """
        effect = ask("key")

        # to_generator should be a method, not a projection program
        to_gen = effect.to_generator
        assert callable(to_gen), "to_generator should be callable"
        assert not isinstance(to_gen, ProgramBase), (
            "to_generator should be a method, not a projection program"
        )

        # Calling it should return a Generator
        result = to_gen()
        assert isinstance(result, Generator), (
            f"to_generator() should return Generator, got {type(result).__name__}"
        )

    def test_program_to_generator_should_return_generator(self) -> None:
        """Programs with actual to_generator should return Generator."""

        @do
        def my_program() -> Generator[Any, Any, int]:
            return 42

        prog = my_program()
        result = prog.to_generator()

        assert isinstance(result, Generator), (
            f"Program.to_generator() should return Generator, got {type(result).__name__}"
        )
        assert not isinstance(result, GeneratorProgram), (
            "Should not return GeneratorProgram"
        )

    def test_effect_to_generator_yields_self(self) -> None:
        """Effect.to_generator() should yield the effect itself."""
        effect = Pure(42)
        gen = effect.to_generator()

        # The first yield should be the effect itself
        yielded = next(gen)
        assert yielded is effect, (
            f"Effect.to_generator() should yield self, got {type(yielded).__name__}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
