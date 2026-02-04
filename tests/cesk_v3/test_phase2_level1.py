from dataclasses import dataclass

import pytest

from doeff.cesk_v3.level1_cesk.frames import ReturnFrame
from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    Done,
    EffectYield,
    Error,
    Failed,
    ProgramControl,
    Value,
)
from doeff.cesk_v3.level1_cesk.step import cesk_step, to_generator


class TestToGenerator:

    def test_converts_iterator(self) -> None:
        def gen():
            yield 1
            yield 2

        g = gen()
        result = to_generator(g)
        assert next(result) == 1
        assert next(result) == 2

    def test_converts_object_with_to_generator_method(self) -> None:
        class FakeProgram:
            def to_generator(self):
                def gen():
                    yield 42

                return gen()

        fp = FakeProgram()
        result = to_generator(fp)
        assert next(result) == 42

    def test_raises_for_non_convertible(self) -> None:
        with pytest.raises(TypeError, match="Cannot convert"):
            to_generator(42)

    def test_string_is_iterable(self) -> None:
        result = to_generator("abc")
        assert next(result) == "a"
        assert next(result) == "b"


class TestCeskStepProgramControl:

    def test_starts_generator_and_yields(self) -> None:
        def program():
            yield "effect1"
            return "done"

        state = CESKState(
            C=ProgramControl(program=program()),
            E={},
            S={},
            K=[],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == "effect1"
        assert len(result.K) == 1
        assert isinstance(result.K[0], ReturnFrame)

    def test_program_returns_immediately(self) -> None:
        def program():
            return 42
            yield  # noqa: B027

        state = CESKState(
            C=ProgramControl(program=program()),
            E={},
            S={},
            K=[],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42
        assert result.K == []

    def test_program_raises_exception(self) -> None:
        def program():
            raise ValueError("program error")
            yield  # noqa: B027

        state = CESKState(
            C=ProgramControl(program=program()),
            E={},
            S={},
            K=[],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert isinstance(result.C.error, ValueError)


class TestCeskStepValueWithK:

    def test_sends_value_to_generator(self) -> None:
        def program():
            x = yield "get_x"
            yield f"got_{x}"
            return x

        gen = program()
        next(gen)

        state = CESKState(
            C=Value(value=42),
            E={},
            S={},
            K=[ReturnFrame(generator=gen)],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == "got_42"

    def test_generator_returns_after_send(self) -> None:
        def program():
            x = yield "get_x"
            return x * 2

        gen = program()
        next(gen)

        state = CESKState(
            C=Value(value=21),
            E={},
            S={},
            K=[ReturnFrame(generator=gen)],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42
        assert result.K == []

    def test_pops_frame_on_return(self) -> None:
        def outer():
            x = yield "outer_effect"
            return x + 1

        def inner():
            return 10
            yield  # noqa: B027

        outer_gen = outer()
        next(outer_gen)

        state = CESKState(
            C=Value(value=5),
            E={},
            S={},
            K=[ReturnFrame(generator=outer_gen)],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 6
        assert len(result.K) == 0


class TestCeskStepErrorWithK:

    def test_throws_error_to_generator(self) -> None:
        def program():
            try:
                yield "effect"
            except ValueError as e:
                yield f"caught: {e}"
                return "handled"

        gen = program()
        next(gen)

        state = CESKState(
            C=Error(error=ValueError("test")),
            E={},
            S={},
            K=[ReturnFrame(generator=gen)],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == "caught: test"

    def test_error_propagates_if_not_caught(self) -> None:
        def program():
            yield "effect"
            return "done"

        gen = program()
        next(gen)

        state = CESKState(
            C=Error(error=RuntimeError("uncaught")),
            E={},
            S={},
            K=[ReturnFrame(generator=gen)],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert isinstance(result.C.error, RuntimeError)
        assert result.K == []


class TestCeskStepTermination:

    def test_value_with_empty_k_returns_done(self) -> None:
        state = CESKState(
            C=Value(value="final"),
            E={},
            S={},
            K=[],
        )

        result = cesk_step(state)

        assert isinstance(result, Done)
        assert result.value == "final"

    def test_error_with_empty_k_returns_failed(self) -> None:
        state = CESKState(
            C=Error(error=RuntimeError("failed")),
            E={},
            S={},
            K=[],
        )

        result = cesk_step(state)

        assert isinstance(result, Failed)
        assert isinstance(result.error, RuntimeError)


class TestCeskStepPreservesEnvironmentAndStore:

    def test_env_and_store_preserved(self) -> None:
        def program():
            yield "effect"

        state = CESKState(
            C=ProgramControl(program=program()),
            E={"env_key": "env_value"},
            S={"store_key": 123},
            K=[],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert result.E == {"env_key": "env_value"}
        assert result.S == {"store_key": 123}
