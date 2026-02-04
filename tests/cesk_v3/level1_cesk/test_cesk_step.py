import pytest

from doeff.cesk_v3.level1_cesk.state import (
    CESKState,
    ProgramControl,
    Value,
    Error,
    EffectYield,
    Done,
    Failed,
)
from doeff.cesk_v3.level1_cesk.frames import ReturnFrame, WithHandlerFrame
from doeff.cesk_v3.level1_cesk.step import cesk_step


class TestProgramControlTransitions:
    def test_program_yielding_effect_produces_effect_yield(self, empty_env, empty_store, empty_k):
        def program_gen():
            yield "some_effect"
            return "done"

        state = CESKState(
            C=ProgramControl(program=program_gen),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == "some_effect"
        assert len(result.K) == 1
        assert isinstance(result.K[0], ReturnFrame)

    def test_program_returning_immediately_produces_value(self, empty_env, empty_store, empty_k):
        def program_gen():
            return 42
            yield  # noqa: unreachable - makes it a generator

        state = CESKState(
            C=ProgramControl(program=program_gen),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42
        assert result.K == []

    def test_program_raising_exception_produces_error(self, empty_env, empty_store, empty_k):
        def program_gen():
            raise ValueError("test error")
            yield  # noqa: unreachable

        state = CESKState(
            C=ProgramControl(program=program_gen),
            E=empty_env,
            S=empty_store,
            K=empty_k,
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert isinstance(result.C.error, ValueError)
        assert str(result.C.error) == "test error"


class TestValueWithKTransitions:
    def test_value_sent_to_generator(self, empty_env, empty_store):
        received_values = []

        def program_gen():
            v1 = yield "effect1"
            received_values.append(v1)
            v2 = yield "effect2"
            received_values.append(v2)
            return "done"

        gen = program_gen()
        next(gen)

        frame = ReturnFrame(generator=gen)
        state = CESKState(
            C=Value(100),
            E=empty_env,
            S=empty_store,
            K=[frame],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, EffectYield)
        assert result.C.yielded == "effect2"
        assert received_values == [100]

    def test_value_causes_generator_to_return(self, empty_env, empty_store):
        def program_gen():
            v = yield "effect"
            return v * 2

        gen = program_gen()
        next(gen)

        frame = ReturnFrame(generator=gen)
        state = CESKState(
            C=Value(21),
            E=empty_env,
            S=empty_store,
            K=[frame],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 42
        assert result.K == []

    def test_value_pops_frame_on_return(self, empty_env, empty_store):
        def outer_gen():
            result = yield "outer_effect"
            return result + 1

        def inner_gen():
            v = yield "inner_effect"
            return v * 2

        outer = outer_gen()
        next(outer)
        inner = inner_gen()
        next(inner)

        state = CESKState(
            C=Value(10),
            E=empty_env,
            S=empty_store,
            K=[ReturnFrame(generator=inner), ReturnFrame(generator=outer)],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == 20
        assert len(result.K) == 1


class TestErrorWithKTransitions:
    def test_error_thrown_to_generator(self, empty_env, empty_store):
        caught_error = []

        def program_gen():
            try:
                yield "effect"
            except ValueError as e:
                caught_error.append(e)
                return "caught"
            return "not caught"

        gen = program_gen()
        next(gen)

        frame = ReturnFrame(generator=gen)
        state = CESKState(
            C=Error(ValueError("test")),
            E=empty_env,
            S=empty_store,
            K=[frame],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.value == "caught"
        assert len(caught_error) == 1

    def test_error_propagates_if_not_caught(self, empty_env, empty_store):
        def program_gen():
            yield "effect"
            return "done"

        gen = program_gen()
        next(gen)

        frame = ReturnFrame(generator=gen)
        state = CESKState(
            C=Error(RuntimeError("uncaught")),
            E=empty_env,
            S=empty_store,
            K=[frame],
        )

        result = cesk_step(state)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert isinstance(result.C.error, RuntimeError)
        assert result.K == []


class TestWithHandlerFrameAssertion:
    def test_assertion_when_k0_is_whf_with_value(self, empty_env, empty_store):
        state = CESKState(
            C=Value(42),
            E=empty_env,
            S=empty_store,
            K=[WithHandlerFrame()],
        )

        with pytest.raises(AssertionError, match="Level 1 only handles ReturnFrame"):
            cesk_step(state)

    def test_assertion_when_k0_is_whf_with_error(self, empty_env, empty_store):
        state = CESKState(
            C=Error(ValueError("test")),
            E=empty_env,
            S=empty_store,
            K=[WithHandlerFrame()],
        )

        with pytest.raises(AssertionError, match="Level 1 only handles ReturnFrame"):
            cesk_step(state)
